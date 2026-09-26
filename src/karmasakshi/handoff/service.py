"""Create, accept, and export multi-agent handoffs.

Delegation, attenuation, grant verification, and evidence packs stay in
the modules that already implement them. This module only sequences those
checks and records which handoff produced which passport.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from karmasakshi.adapters.base import CommitResult, OutcomeProof
from karmasakshi.config.clock import SYSTEM_CLOCK, Clock
from karmasakshi.crypto.keyring import Keyring
from karmasakshi.crypto.keys import SigningKey, VerificationKey
from karmasakshi.delegation.chain import verify_delegation_chain
from karmasakshi.delegation.revocation import MAX_DELEGATION_DEPTH, assert_no_revoked_ancestors
from karmasakshi.domain.common import Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.domain.seal import SealedManifest
from karmasakshi.engine.core import KarmaSakshiEngine
from karmasakshi.errors import HandoffRejectedError, KarmaSakshiError
from karmasakshi.grants.model import ExecutionGrant, ScopeConstraints
from karmasakshi.grants.verifier import verify_grant
from karmasakshi.handoff.model import HandoffAcceptance, HandoffEnvelope, WorkflowExport
from karmasakshi.passports.generator import build_passport
from karmasakshi.passports.v2 import ActionPassportV2, upgrade_passport_v1_to_v2
from karmasakshi.portable.builder import build_evidence_pack
from karmasakshi.portable.model import EmbeddedVerificationKey, EvidencePack
from karmasakshi.portable.verify import (
    EvidencePackVerificationResult,
    verify_evidence_pack,
)
from karmasakshi.stores.memory import InMemoryGrantStore

_PLACEHOLDER_HASH = "sha256:" + ("0" * 64)


@dataclass
class WorkflowRecord:
    """In-process list of handoffs and the passports they led to.

    Persisted form is :class:`WorkflowExport`. This object is not itself
    hashed; ``export_workflow`` freezes a copy and hashes that.
    """

    workflow_id: str
    root_grant: ExecutionGrant
    created_at: datetime
    handoffs: list[HandoffEnvelope] = field(default_factory=list)
    passports: list[ActionPassportV2] = field(default_factory=list)
    evidence_packs: list[EvidencePack] = field(default_factory=list)

    def grant_ids(self) -> set[str]:
        ids = {self.root_grant.grant_id}
        ids.update(envelope.grant.grant_id for envelope in self.handoffs)
        return ids


@dataclass(frozen=True)
class WorkflowVerificationResult:
    """Offline verdict for one :class:`WorkflowExport`."""

    export_hash_verified: bool
    handoffs_bound: bool
    evidence_packs_verified: bool
    all_verified: bool
    reasons: tuple[str, ...] = ()
    evidence_pack_results: tuple[EvidencePackVerificationResult, ...] = ()


def open_workflow(
    engine: KarmaSakshiEngine,
    root_grant: ExecutionGrant,
    *,
    workflow_id: str | None = None,
) -> WorkflowRecord:
    """Start a workflow from a human- or service-issued root grant.

    Records the root as a lineage root (parent ``None``) when the store
    does not already have that row. ``engine.authorize`` does not write a
    root row; without one, ``assert_no_revoked_ancestors`` cannot tell a
    root from a missing parent and fails closed.
    """
    if root_grant.parent_grant_id is not None:
        raise HandoffRejectedError(
            f"grant {root_grant.grant_id} is not a root; a workflow must start "
            "at a grant with no parent"
        )
    if root_grant.subject.principal_type != PrincipalType.AGENT:
        raise HandoffRejectedError("the workflow root grant must be addressed to an agent")
    _ensure_root_lineage(engine, root_grant)
    return WorkflowRecord(
        workflow_id=workflow_id or str(uuid.uuid4()),
        root_grant=root_grant,
        created_at=engine.context.clock.now(),
    )


def create_handoff(
    engine: KarmaSakshiEngine,
    parent_grant: ExecutionGrant,
    to_agent: Principal,
    narrowed_scope: ScopeConstraints | None = None,
    *,
    workflow: WorkflowRecord,
    issuer: Principal,
    signing_key: SigningKey,
    task: str,
    from_agent: Principal | None = None,
    allowed_effect_types: tuple[str, ...] | None = None,
    audience: tuple[str, ...] | None = None,
    max_uses: int | None = None,
    not_before: datetime | None = None,
    expires_at: datetime | None = None,
    manifest_hash: str | None = None,
    grant_id: str | None = None,
    nonce: str | None = None,
    handoff_id: str | None = None,
) -> HandoffEnvelope:
    """Ask the engine to delegate a narrower grant and wrap it in an envelope.

    The issuer must be a human or service principal. ``engine.delegate``
    enforces that, and also enforces attenuation, budget inheritance, and
    the parent's time window. This function does not sign grants itself.
    """
    sender = from_agent if from_agent is not None else parent_grant.subject
    if sender.principal_type != PrincipalType.AGENT:
        raise HandoffRejectedError("from_agent must be an agent principal")
    if to_agent.principal_type != PrincipalType.AGENT:
        raise HandoffRejectedError("to_agent must be an agent principal")
    if sender.principal_id != parent_grant.subject.principal_id:
        raise HandoffRejectedError(
            f"from_agent {sender.principal_id} is not the subject of parent grant "
            f"{parent_grant.grant_id}"
        )

    parent_lineage = _parent_lineage(workflow, parent_grant)
    parent_depth = len(parent_lineage) - 1
    child_depth = parent_depth + 1
    if child_depth > MAX_DELEGATION_DEPTH:
        raise HandoffRejectedError(
            f"delegation depth {child_depth} exceeds max depth {MAX_DELEGATION_DEPTH}"
        )

    _ensure_root_lineage(engine, workflow.root_grant)
    # Let issue_grant reject an agent issuer. Do not pre-empt that error.
    child = engine.delegate(
        parent_grant,
        issuer=issuer,
        subject=to_agent,
        signing_key=signing_key,
        grant_id=grant_id,
        nonce=nonce,
        audience=audience,
        allowed_effect_types=allowed_effect_types,
        scope=narrowed_scope,
        not_before=not_before,
        expires_at=expires_at,
        max_uses=max_uses,
        manifest_hash=manifest_hash,
    )

    now = engine.context.clock.now()
    envelope_expiry = child.expires_at
    draft = HandoffEnvelope(
        handoff_id=handoff_id or str(uuid.uuid4()),
        workflow_id=workflow.workflow_id,
        from_agent=sender,
        to_agent=to_agent,
        task=task,
        grant=child,
        parent_grant_id=parent_grant.grant_id,
        delegation_depth=child_depth,
        lineage=(*parent_lineage, child),
        created_at=now,
        expires_at=envelope_expiry,
        content_hash=_PLACEHOLDER_HASH,
    )
    envelope = draft.model_copy(update={"content_hash": draft.compute_content_hash()})
    workflow.handoffs.append(envelope)
    return envelope


def assert_handoff_ready_for_execute(
    engine: KarmaSakshiEngine,
    envelope: HandoffEnvelope,
    grant: ExecutionGrant,
    acceptance: HandoffAcceptance,
    *,
    workflow_id: str,
) -> HandoffEnvelope:
    """Fail closed unless this accepted handoff is the one being executed.

    The acceptance record is what proves the executing agent already called
    ``accept_handoff``. This function checks that record, then calls
    ``accept_handoff`` again so expiry, signature, chain, and revocation are
    still enforced at execute time.
    """
    if envelope.workflow_id != workflow_id or acceptance.workflow_id != workflow_id:
        raise HandoffRejectedError(
            f"handoff {envelope.handoff_id} belongs to workflow {envelope.workflow_id}, "
            f"not {workflow_id}"
        )
    if acceptance.handoff_id != envelope.handoff_id:
        raise HandoffRejectedError(
            f"acceptance record is for handoff {acceptance.handoff_id}, not {envelope.handoff_id}"
        )
    if acceptance.accepted_by != envelope.to_agent.principal_id:
        raise HandoffRejectedError(
            f"handoff {envelope.handoff_id} was not accepted by the executing agent "
            f"{envelope.to_agent.principal_id}"
        )
    if (
        acceptance.content_hash != envelope.content_hash
        or envelope.compute_content_hash() != envelope.content_hash
    ):
        raise HandoffRejectedError(
            f"handoff {envelope.handoff_id} content hash does not match the acceptance record"
        )
    if (
        grant.grant_id != envelope.grant.grant_id
        or grant.canonical_hash() != envelope.grant.canonical_hash()
    ):
        raise HandoffRejectedError(
            f"grant {grant.grant_id} is not the delegated grant on handoff {envelope.handoff_id}"
        )
    receiver = Principal(principal_id=acceptance.accepted_by, principal_type=PrincipalType.AGENT)
    return accept_handoff(engine, envelope, receiver)


def accept_handoff(
    engine: KarmaSakshiEngine,
    envelope: HandoffEnvelope,
    receiving_agent: Principal,
) -> HandoffEnvelope:
    """Fail closed unless this agent may act on this envelope.

    Checks, in order: canonical hash, addressee, grant time window and
    signature, the full delegation chain, and recorded ancestor revocation.
    Any failure raises :class:`HandoffRejectedError`.
    """
    try:
        _accept(engine, envelope, receiving_agent)
    except HandoffRejectedError:
        raise
    except KarmaSakshiError as exc:
        raise HandoffRejectedError(str(exc)) from exc
    return envelope


def record_effect(
    workflow: WorkflowRecord,
    *,
    engine: KarmaSakshiEngine,
    sealed: SealedManifest,
    grant: ExecutionGrant,
    commit_result: CommitResult,
    outcome_proof: OutcomeProof,
) -> EvidencePack:
    """Build an Action Passport and an Evidence Pack for one committed effect.

    The grant must already belong to this workflow. The pack is produced by
    ``build_evidence_pack`` and stored on the workflow record.
    """
    if grant.grant_id not in workflow.grant_ids():
        raise HandoffRejectedError(
            f"grant {grant.grant_id} is not part of workflow {workflow.workflow_id}"
        )
    lifecycle_state = engine.get_lifecycle_state(sealed.manifest.manifest_id).value
    passport_v1 = build_passport(
        sealed=sealed,
        keyring=engine.context.keyring,
        audit=engine.context.audit,
        lifecycle_state=lifecycle_state,
        grant=grant,
        grant_store=engine.context.grant_store,
        commit_result=commit_result,
        outcome_proof=outcome_proof,
        clock=engine.context.clock,
    )
    passport = upgrade_passport_v1_to_v2(passport_v1, tenant_id=engine.context.tenant_id)
    pack = build_evidence_pack(
        passport=passport,
        sealed_manifest=sealed,
        audit=engine.context.audit,
        keyring=engine.context.keyring,
        grant=grant,
        clock=engine.context.clock,
    )
    workflow.passports.append(passport)
    workflow.evidence_packs.append(pack)
    return pack


def export_workflow(
    workflow: WorkflowRecord,
    *,
    keyring: Keyring,
    clock: Clock = SYSTEM_CLOCK,
) -> WorkflowExport:
    """Freeze the workflow into one offline-verifiable document."""
    key_ids = keyring.key_ids()
    embedded = tuple(
        EmbeddedVerificationKey(
            key_id=key_id,
            algorithm=keyring.get(key_id).algorithm,
            public_key_b64=keyring.get(key_id).public_bytes_b64(),
        )
        for key_id in key_ids
    )
    draft = WorkflowExport(
        generated_at=clock.now(),
        export_hash=_PLACEHOLDER_HASH,
        workflow_id=workflow.workflow_id,
        created_at=workflow.created_at,
        root_grant=workflow.root_grant,
        handoffs=tuple(workflow.handoffs),
        passports=tuple(workflow.passports),
        evidence_packs=tuple(workflow.evidence_packs),
        verification_keys=embedded,
    )
    return draft.model_copy(update={"export_hash": draft.compute_export_hash()})


def verify_workflow_export(
    export: WorkflowExport,
    *,
    revoked_grant_ids: set[str] | None = None,
) -> WorkflowVerificationResult:
    """Check one export offline.

    Evidence packs are passed to ``verify_evidence_pack``. Handoffs are
    checked against this workflow's id and root grant, so a handoff taken
    from another workflow does not verify even if that handoff is
    internally consistent.

    ``revoked_grant_ids`` is optional. A historical export does not carry
    the live revocation set: revocation is enforced at ``accept_handoff``
    and again at ``commit``. Passing a list here is an extra check a
    reviewer opts into. Omitting it leaves the verdict unchanged.
    """
    reasons: list[str] = []
    export_hash_verified = export.compute_export_hash() == export.export_hash
    if not export_hash_verified:
        reasons.append("export_hash does not match canonical content")

    handoffs_bound = True
    seen_ids: set[str] = set()
    chain_store = _lineage_store(export)
    keyring = _keyring_from_export(export)
    if keyring is None:
        handoffs_bound = False
        reasons.append("verification_keys: could not rebuild a keyring")

    for envelope in export.handoffs:
        if envelope.handoff_id in seen_ids:
            handoffs_bound = False
            reasons.append(f"duplicate handoff_id {envelope.handoff_id}")
        seen_ids.add(envelope.handoff_id)
        if envelope.compute_content_hash() != envelope.content_hash:
            handoffs_bound = False
            reasons.append(f"handoff {envelope.handoff_id} content hash mismatch")
        if envelope.workflow_id != export.workflow_id:
            handoffs_bound = False
            reasons.append(
                f"handoff {envelope.handoff_id} belongs to workflow "
                f"{envelope.workflow_id}, not {export.workflow_id}"
            )
        if not envelope.lineage or envelope.lineage[0].grant_id != export.root_grant.grant_id:
            handoffs_bound = False
            reasons.append(
                f"handoff {envelope.handoff_id} does not descend from this workflow's root grant"
            )
        elif envelope.lineage[0].canonical_hash() != export.root_grant.canonical_hash():
            handoffs_bound = False
            reasons.append(
                f"handoff {envelope.handoff_id} root grant bytes do not match this workflow"
            )
        if keyring is not None and envelope.workflow_id == export.workflow_id:
            try:
                verify_delegation_chain(
                    list(envelope.lineage),
                    keyring=keyring,
                    grant_store=chain_store,
                    now=export.generated_at,
                )
            except KarmaSakshiError as exc:
                handoffs_bound = False
                reasons.append(f"handoff {envelope.handoff_id} chain: {exc}")

    if revoked_grant_ids:
        chain_ids = {export.root_grant.grant_id}
        for envelope in export.handoffs:
            chain_ids.update(grant.grant_id for grant in envelope.lineage)
        listed = sorted(chain_ids & revoked_grant_ids)
        if listed:
            handoffs_bound = False
            reasons.append("revoked grant(s) in chain: " + ", ".join(listed))

    pack_results: list[EvidencePackVerificationResult] = []
    packs_ok = True
    passport_by_manifest = {passport.manifest_id: passport for passport in export.passports}
    workflow_grant_ids = {export.root_grant.grant_id}
    workflow_grant_ids.update(envelope.grant.grant_id for envelope in export.handoffs)
    for pack in export.evidence_packs:
        result = verify_evidence_pack(pack)
        pack_results.append(result)
        if not result.all_verified:
            packs_ok = False
            reasons.append(f"evidence pack {pack.manifest_id} failed: {'; '.join(result.reasons)}")
        if pack.grant is None or pack.grant.grant_id not in workflow_grant_ids:
            packs_ok = False
            reasons.append(f"evidence pack {pack.manifest_id} grant is not part of this workflow")
        passport = passport_by_manifest.get(pack.manifest_id)
        if passport is None or passport.passport_hash != pack.passport.passport_hash:
            packs_ok = False
            reasons.append(
                f"evidence pack {pack.manifest_id} passport is not linked from this workflow"
            )

    for passport in export.passports:
        if passport.grant_id is not None and passport.grant_id not in workflow_grant_ids:
            packs_ok = False
            reasons.append(
                f"passport {passport.manifest_id} cites a grant from outside the workflow"
            )

    all_verified = export_hash_verified and handoffs_bound and packs_ok and not reasons
    return WorkflowVerificationResult(
        export_hash_verified=export_hash_verified,
        handoffs_bound=handoffs_bound,
        evidence_packs_verified=packs_ok,
        all_verified=all_verified,
        reasons=tuple(reasons),
        evidence_pack_results=tuple(pack_results),
    )


def workflow_from_export(export: WorkflowExport) -> WorkflowRecord:
    """Reload a saved export into a record that can accept more handoffs."""
    return WorkflowRecord(
        workflow_id=export.workflow_id,
        root_grant=export.root_grant,
        created_at=export.created_at,
        handoffs=list(export.handoffs),
        passports=list(export.passports),
        evidence_packs=list(export.evidence_packs),
    )


def _accept(
    engine: KarmaSakshiEngine,
    envelope: HandoffEnvelope,
    receiving_agent: Principal,
) -> None:
    if envelope.compute_content_hash() != envelope.content_hash:
        raise HandoffRejectedError(
            f"handoff {envelope.handoff_id} content hash does not match; envelope was modified"
        )

    if receiving_agent.principal_type != PrincipalType.AGENT:
        raise HandoffRejectedError("receiving principal must be an agent")
    if (
        receiving_agent.principal_id != envelope.to_agent.principal_id
        or receiving_agent.principal_type != envelope.to_agent.principal_type
    ):
        raise HandoffRejectedError(
            f"handoff {envelope.handoff_id} is addressed to {envelope.to_agent.principal_id}, "
            f"not {receiving_agent.principal_id}"
        )
    if envelope.grant.subject.principal_id != receiving_agent.principal_id:
        raise HandoffRejectedError(
            f"grant {envelope.grant.grant_id} is not addressed to {receiving_agent.principal_id}"
        )

    now = engine.context.clock.now()
    leeway = timedelta(seconds=engine.context.clock_skew.leeway_seconds)
    if now > envelope.expires_at + leeway:
        raise HandoffRejectedError(
            f"handoff {envelope.handoff_id} expired at {envelope.expires_at.isoformat()}"
        )

    verify_grant(envelope.grant, engine.context.keyring, now=now, leeway=leeway)
    verify_delegation_chain(
        list(envelope.lineage),
        keyring=engine.context.keyring,
        grant_store=engine.context.grant_store,
        now=now,
        leeway=leeway,
    )
    assert_no_revoked_ancestors(envelope.grant, engine.context.grant_store)


def _ensure_root_lineage(engine: KarmaSakshiEngine, root_grant: ExecutionGrant) -> None:
    store = engine.context.grant_store
    if root_grant.parent_grant_id is not None:
        return
    if not store.has_lineage(root_grant.grant_id):
        store.record_lineage(root_grant.grant_id, None)


def _parent_lineage(
    workflow: WorkflowRecord, parent_grant: ExecutionGrant
) -> tuple[ExecutionGrant, ...]:
    if parent_grant.grant_id == workflow.root_grant.grant_id:
        if parent_grant.canonical_hash() != workflow.root_grant.canonical_hash():
            raise HandoffRejectedError("parent grant does not match the workflow root")
        return (workflow.root_grant,)
    for envelope in workflow.handoffs:
        if envelope.grant.grant_id == parent_grant.grant_id:
            if envelope.grant.canonical_hash() != parent_grant.canonical_hash():
                raise HandoffRejectedError(
                    f"parent grant {parent_grant.grant_id} does not match the recorded handoff"
                )
            return envelope.lineage
    raise HandoffRejectedError(
        f"parent grant {parent_grant.grant_id} is not in workflow {workflow.workflow_id}; "
        "missing lineage"
    )


def _lineage_store(export: WorkflowExport) -> InMemoryGrantStore:
    """Replay parent pointers so chain verification can see the recorded roots.

    This store is not the live revocation set. Offline verification checks
    structure and signatures; live revocation is ``accept_handoff``'s job.
    """
    store = InMemoryGrantStore()
    store.record_lineage(export.root_grant.grant_id, export.root_grant.parent_grant_id)
    for envelope in export.handoffs:
        for grant in envelope.lineage:
            if grant.parent_grant_id is None and not store.has_lineage(grant.grant_id):
                store.record_lineage(grant.grant_id, None)
            elif grant.parent_grant_id is not None:
                store.record_lineage(grant.grant_id, grant.parent_grant_id)
    return store


def _keyring_from_export(export: WorkflowExport) -> Keyring | None:
    try:
        keys = [
            VerificationKey.from_public_b64(item.key_id, item.public_key_b64, item.algorithm)
            for item in export.verification_keys
        ]
        if not keys:
            for pack in export.evidence_packs:
                keys.extend(
                    VerificationKey.from_public_b64(
                        item.key_id, item.public_key_b64, item.algorithm
                    )
                    for item in pack.verification_keys
                )
        if not keys:
            return None
        return Keyring(keys)
    except (KarmaSakshiError, ValueError):
        return None


__all__ = [
    "WorkflowRecord",
    "WorkflowVerificationResult",
    "accept_handoff",
    "assert_handoff_ready_for_execute",
    "create_handoff",
    "export_workflow",
    "open_workflow",
    "record_effect",
    "verify_workflow_export",
    "workflow_from_export",
]

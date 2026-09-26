"""The CLI's local workspace: where dev keys, sealed manifests, grants, the
durable grant store, and the audit journal live between separate CLI
invocations (each ``karmasakshi`` command is its own process).

Default location is ``.karmasakshi`` under the current directory, overridable
with ``--workspace`` or the ``KARMASAKSHI_HOME`` environment variable.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime
from pathlib import Path

from karmasakshi.adapters.base import CommitResult, CompensationResult, OutcomeProof
from karmasakshi.approval.model import ApprovalStatement
from karmasakshi.audit.journal import AuditJournal
from karmasakshi.audit.sqlite_backend import SQLiteAuditBackend
from karmasakshi.causal import CausalEffectGraph
from karmasakshi.compensation.passport import CompensationPassport
from karmasakshi.crypto.keyring import Keyring
from karmasakshi.crypto.keys import (
    SigningKey,
    VerificationKey,
    generate_signing_key,
    load_signing_key_from_file,
    save_signing_key_to_file,
)
from karmasakshi.domain.manifest import EffectManifest
from karmasakshi.domain.seal import SealedManifest
from karmasakshi.engine.context import EngineContext
from karmasakshi.engine.core import KarmaSakshiEngine
from karmasakshi.envelope.model import DecisionEnvelope
from karmasakshi.errors import KeyLoadError
from karmasakshi.grants.model import ExecutionGrant
from karmasakshi.handoff.model import HandoffEnvelope, WorkflowExport
from karmasakshi.intelligence.model import EffectAssessment
from karmasakshi.outbox.sqlite import SQLiteOutboxStore
from karmasakshi.policy.bundle import PolicyBundle, SealedPolicyBundle
from karmasakshi.state_machine.states import LifecycleState
from karmasakshi.stores.lifecycle_sqlite import SQLiteLifecycleStore
from karmasakshi.stores.sqlite import SQLiteGrantStore
from karmasakshi.witness.model import WitnessStatement

DEFAULT_WORKSPACE_ENV = "KARMASAKSHI_HOME"
DEFAULT_WORKSPACE_DIRNAME = ".karmasakshi"


def default_workspace_path() -> Path:
    env = os.environ.get(DEFAULT_WORKSPACE_ENV)
    if env:
        return Path(env)
    return Path.cwd() / DEFAULT_WORKSPACE_DIRNAME


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.keys_dir = self.root / "keys"
        self.manifests_dir = self.root / "manifests"
        self.grants_dir = self.root / "grants"
        self.policies_dir = self.root / "policies"
        self.approvals_dir = self.root / "approvals"
        self.witnesses_dir = self.root / "witnesses"
        self.causal_graphs_dir = self.root / "causal-graphs"
        self.envelopes_dir = self.root / "envelopes"
        self.handoffs_dir = self.root / "handoffs"
        self.workflows_dir = self.root / "workflows"

    def ensure_initialized(self) -> None:
        for d in (
            self.root,
            self.keys_dir,
            self.manifests_dir,
            self.grants_dir,
            self.policies_dir,
            self.approvals_dir,
            self.witnesses_dir,
            self.causal_graphs_dir,
            self.envelopes_dir,
            self.handoffs_dir,
            self.workflows_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def is_initialized(self) -> bool:
        return self.root.exists() and self.keys_dir.exists()

    @property
    def agenteval_memory_path(self) -> Path:
        return self.root / "agenteval-memory.jsonl"

    # --- keys ---------------------------------------------------------------

    def _pubkey_path(self, key_id: str) -> Path:
        return self.keys_dir / f"{key_id}.pub.json"

    def _privkey_path(self, key_id: str) -> Path:
        return self.keys_dir / f"{key_id}.priv"

    def has_key(self, key_id: str) -> bool:
        return self._pubkey_path(key_id).exists()

    def generate_key(self, key_id: str) -> SigningKey:
        key = generate_signing_key(key_id)
        save_signing_key_to_file(key, self._privkey_path(key_id))
        self._pubkey_path(key_id).write_text(
            json.dumps(
                {
                    "key_id": key_id,
                    "algorithm": key.algorithm,
                    "public_b64": key.verification_key().public_bytes_b64(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return key

    def load_signing_key(self, key_id: str) -> SigningKey:
        path = self._privkey_path(key_id)
        if not path.exists():
            raise KeyLoadError(f"no private key found for key_id={key_id!r} in {self.keys_dir}")
        return load_signing_key_from_file(path, key_id)

    def list_key_ids(self) -> list[str]:
        return sorted(p.stem.removesuffix(".pub") for p in self.keys_dir.glob("*.pub.json"))

    def load_keyring(self) -> Keyring:
        keys = []
        for path in sorted(self.keys_dir.glob("*.pub.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            keys.append(
                VerificationKey.from_public_b64(
                    data["key_id"], data["public_b64"], data.get("algorithm", "ed25519")
                )
            )
        return Keyring(keys)

    # --- manifests / seals ----------------------------------------------------

    def save_sealed_manifest(self, sealed: SealedManifest) -> Path:
        path = self.manifests_dir / f"{sealed.manifest.manifest_id}.json"
        path.write_text(sealed.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_sealed_manifest(self, manifest_id: str) -> SealedManifest:
        path = self.manifests_dir / f"{manifest_id}.json"
        return SealedManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def save_unsealed_manifest(self, manifest: EffectManifest) -> Path:
        path = self.manifests_dir / f"{manifest.manifest_id}.unsealed.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_unsealed_manifest(self, manifest_id: str) -> EffectManifest:
        path = self.manifests_dir / f"{manifest_id}.unsealed.json"
        return EffectManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def load_manifest_any(self, manifest_id: str) -> EffectManifest:
        """Load a manifest by id, preferring the sealed copy (if ``seal`` has
        already run) and falling back to the unsealed one -- ``assess()``
        does not require a manifest to be sealed first."""
        if (self.manifests_dir / f"{manifest_id}.json").exists():
            return self.load_sealed_manifest(manifest_id).manifest
        return self.load_unsealed_manifest(manifest_id)

    # --- assessments ----------------------------------------------------------

    def save_assessment(self, assessment: EffectAssessment) -> Path:
        path = self.manifests_dir / f"{assessment.manifest_id}.assessment.json"
        path.write_text(assessment.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_assessment(self, manifest_id: str) -> EffectAssessment | None:
        path = self.manifests_dir / f"{manifest_id}.assessment.json"
        if not path.exists():
            return None
        return EffectAssessment.model_validate_json(path.read_text(encoding="utf-8"))

    # --- causal effect graphs -------------------------------------------------

    def save_causal_graph(self, graph: CausalEffectGraph) -> Path:
        path = self.causal_graphs_dir / f"{graph.graph_id}.json"
        path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_causal_graph(self, graph_id: str) -> CausalEffectGraph:
        path = self.causal_graphs_dir / f"{graph_id}.json"
        return CausalEffectGraph.model_validate_json(path.read_text(encoding="utf-8"))

    # --- decision envelopes (extreme-v2 Phase 6) ------------------------------

    def save_decision_envelope(self, envelope: DecisionEnvelope) -> Path:
        path = self.envelopes_dir / f"{envelope.envelope_id}.json"
        path.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_decision_envelope(self, envelope_id: str) -> DecisionEnvelope:
        path = self.envelopes_dir / f"{envelope_id}.json"
        return DecisionEnvelope.model_validate_json(path.read_text(encoding="utf-8"))

    # --- policy bundles ---------------------------------------------------------

    def save_unsigned_policy_bundle(self, bundle: PolicyBundle) -> Path:
        path = self.policies_dir / f"{bundle.bundle_id}.unsigned.json"
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_unsigned_policy_bundle(self, bundle_id: str) -> PolicyBundle:
        path = self.policies_dir / f"{bundle_id}.unsigned.json"
        return PolicyBundle.model_validate_json(path.read_text(encoding="utf-8"))

    def save_sealed_policy_bundle(self, sealed: SealedPolicyBundle) -> Path:
        path = self.policies_dir / f"{sealed.bundle.bundle_id}.json"
        path.write_text(sealed.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_sealed_policy_bundle(self, bundle_id: str) -> SealedPolicyBundle:
        path = self.policies_dir / f"{bundle_id}.json"
        return SealedPolicyBundle.model_validate_json(path.read_text(encoding="utf-8"))

    # --- approval statements (extreme-v2 Phase 3) ------------------------------

    def save_approval_statement(self, statement: ApprovalStatement) -> Path:
        path = (
            self.approvals_dir
            / f"{statement.manifest_hash.removeprefix('sha256:')}.{statement.statement_id}.json"
        )
        path.write_text(statement.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_approval_statements(self, manifest_hash: str) -> tuple[ApprovalStatement, ...]:
        prefix = manifest_hash.removeprefix("sha256:")
        statements = []
        for path in sorted(self.approvals_dir.glob(f"{prefix}.*.json")):
            statements.append(
                ApprovalStatement.model_validate_json(path.read_text(encoding="utf-8"))
            )
        return tuple(statements)

    # --- witness statements (extreme-v2 Phase 9) -------------------------------

    def save_witness_statement(self, statement: WitnessStatement) -> Path:
        path = (
            self.witnesses_dir
            / f"{statement.manifest_hash.removeprefix('sha256:')}.{statement.statement_id}.json"
        )
        path.write_text(statement.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_witness_statements(self, manifest_hash: str) -> tuple[WitnessStatement, ...]:
        prefix = manifest_hash.removeprefix("sha256:")
        statements = []
        for path in sorted(self.witnesses_dir.glob(f"{prefix}.*.json")):
            statements.append(
                WitnessStatement.model_validate_json(path.read_text(encoding="utf-8"))
            )
        return tuple(statements)

    # --- grants -------------------------------------------------------------

    def save_grant(self, grant: ExecutionGrant) -> Path:
        path = self.grants_dir / f"{grant.grant_id}.json"
        path.write_text(grant.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_grant(self, grant_id: str) -> ExecutionGrant:
        path = self.grants_dir / f"{grant_id}.json"
        return ExecutionGrant.model_validate_json(path.read_text(encoding="utf-8"))

    # --- handoffs / workflows -------------------------------------------------

    def save_handoff(self, envelope: HandoffEnvelope) -> Path:
        self.handoffs_dir.mkdir(parents=True, exist_ok=True)
        path = self.handoffs_dir / f"{envelope.handoff_id}.json"
        path.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_handoff(self, handoff_id: str) -> HandoffEnvelope:
        path = self.handoffs_dir / f"{handoff_id}.json"
        return HandoffEnvelope.model_validate_json(path.read_text(encoding="utf-8"))

    def save_workflow_export(self, export: WorkflowExport) -> Path:
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        path = self.workflows_dir / f"{export.workflow_id}.json"
        path.write_text(export.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_workflow_export(self, workflow_id: str) -> WorkflowExport:
        path = self.workflows_dir / f"{workflow_id}.json"
        return WorkflowExport.model_validate_json(path.read_text(encoding="utf-8"))

    def has_workflow(self, workflow_id: str) -> bool:
        return (self.workflows_dir / f"{workflow_id}.json").exists()

    # --- commit results -------------------------------------------------------

    def save_commit_result(self, manifest_id: str, result: CommitResult) -> Path:
        path = self.manifests_dir / f"{manifest_id}.commit.json"
        path.write_text(json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8")
        return path

    def load_commit_result(self, manifest_id: str) -> CommitResult | None:
        path = self.manifests_dir / f"{manifest_id}.commit.json"
        if not path.exists():
            return None
        return CommitResult(**json.loads(path.read_text(encoding="utf-8")))

    def save_outcome_proof(self, manifest_id: str, proof: OutcomeProof) -> Path:
        path = self.manifests_dir / f"{manifest_id}.proof.json"
        path.write_text(
            json.dumps(dataclasses.asdict(proof), default=str, indent=2), encoding="utf-8"
        )
        return path

    def load_outcome_proof(self, manifest_id: str) -> OutcomeProof | None:
        path = self.manifests_dir / f"{manifest_id}.proof.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data["observed_at"] = datetime.fromisoformat(data["observed_at"])
        return OutcomeProof(**data)

    def save_compensation_result(self, manifest_id: str, result: CompensationResult) -> Path:
        path = self.manifests_dir / f"{manifest_id}.compensation.json"
        path.write_text(json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8")
        return path

    def load_compensation_result(self, manifest_id: str) -> CompensationResult | None:
        path = self.manifests_dir / f"{manifest_id}.compensation.json"
        if not path.exists():
            return None
        return CompensationResult(**json.loads(path.read_text(encoding="utf-8")))

    def save_compensation_passport(self, passport: CompensationPassport) -> Path:
        path = (
            self.manifests_dir / f"{passport.compensation_manifest_id}.compensation-passport.json"
        )
        path.write_text(passport.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load_compensation_passport(self, compensation_manifest_id: str) -> CompensationPassport:
        path = self.manifests_dir / f"{compensation_manifest_id}.compensation-passport.json"
        return CompensationPassport.model_validate_json(path.read_text(encoding="utf-8"))

    # --- durable stores -------------------------------------------------------

    def open_grant_store(self) -> SQLiteGrantStore:
        return SQLiteGrantStore(self.root / "grants.db")

    def open_lifecycle_store(self) -> SQLiteLifecycleStore:
        return SQLiteLifecycleStore(self.root / "lifecycle.db")

    def open_outbox_store(self) -> SQLiteOutboxStore:
        return SQLiteOutboxStore(self.root / "outbox.db")

    def open_audit(self) -> AuditJournal:
        return AuditJournal(backend=SQLiteAuditBackend(self.root / "audit.db"))

    def build_engine(self) -> KarmaSakshiEngine:
        ctx = EngineContext(
            keyring=self.load_keyring(),
            grant_store=self.open_grant_store(),
            audit=self.open_audit(),
            lifecycle_store=self.open_lifecycle_store(),
            outbox_store=self.open_outbox_store(),
        )
        return KarmaSakshiEngine(ctx)

    def reconstruct_lifecycle_state(self, engine: KarmaSakshiEngine, manifest_id: str) -> None:
        """Seed ``engine``'s lifecycle record for ``manifest_id``.

        Prefer the Phase 13 durable lifecycle store when it already holds a
        state (authoritative for the next transition). Fall back to the
        last audit ``to_state`` for workspaces that pre-date lifecycle.db.
        """
        store = engine.context.lifecycle_store
        if store is not None:
            stored = store.get(manifest_id)
            if stored is not None:
                engine.seed_lifecycle_state(manifest_id, stored)
                return
        events = [e for e in engine.context.audit.events_for_manifest(manifest_id) if e.to_state]
        if not events:
            return
        last = events[-1]
        assert last.to_state is not None  # nosec B101 - guaranteed by the filter above, not a security check
        engine.seed_lifecycle_state(manifest_id, LifecycleState(last.to_state))


__all__ = ["DEFAULT_WORKSPACE_ENV", "Workspace", "default_workspace_path"]

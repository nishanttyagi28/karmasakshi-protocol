from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import typer

from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.domain.common import MonetaryAmount, Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.duty.roles import RoleAssignment
from karmasakshi.grants.model import ScopeConstraints
from karmasakshi.grants.verifier import verify_grant

grant_app = typer.Typer(help="Issue, verify, delegate, revoke, and inspect Execution Grants.")


def _parse_role_assignment(manifest_hash: str, role_entries: list[str]) -> RoleAssignment | None:
    """Parse repeatable ``--role role_name:principal_id`` CLI options into
    a :class:`RoleAssignment`, or ``None`` if none were given."""
    if not role_entries:
        return None
    assignments: list[tuple[str, str]] = []
    for entry in role_entries:
        role, sep, principal_id = entry.partition(":")
        if not sep:
            raise typer.BadParameter(f"--role must be 'role_name:principal_id', got {entry!r}")
        assignments.append((role, principal_id))
    return RoleAssignment(manifest_hash=manifest_hash, assignments=tuple(assignments))


def _issue_scope(
    *,
    max_amount_minor: int | None,
    currency: str,
    allowed_recipient: list[str],
) -> ScopeConstraints:
    """Map CLI scope flags onto ``ScopeConstraints``.

    No flags means an unrestricted scope, which is what ``grant issue``
    did before these flags existed.
    """
    if max_amount_minor is None and not allowed_recipient:
        return ScopeConstraints()
    return ScopeConstraints(
        max_amount=(
            MonetaryAmount(currency=currency, minor_units=max_amount_minor)
            if max_amount_minor is not None
            else None
        ),
        recipients=tuple(allowed_recipient) if allowed_recipient else None,
    )


@grant_app.command("issue")
def issue(
    ctx: typer.Context,
    manifest_id: Annotated[str, typer.Argument()],
    issuer_id: Annotated[str, typer.Option()],
    subject_id: Annotated[str, typer.Option()],
    key_id: Annotated[str, typer.Option(help="Signing key id in the workspace")],
    issuer_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.HUMAN,
    subject_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.AGENT,
    audience: Annotated[
        list[str], typer.Option("--audience", help="Allowed adapter_id, repeatable")
    ] = [],  # noqa: B006
    max_uses: Annotated[int, typer.Option()] = 1,
    ttl_seconds: Annotated[int, typer.Option()] = 300,
    policy_bundle_id: Annotated[
        str | None,
        typer.Option(
            "--policy-bundle-id",
            help="A sealed policy bundle (from `policy sign`) to bind into this grant; "
            "the same bundle must be presented again at `execute` time",
        ),
    ] = None,
    separation_policy_bundle_id: Annotated[
        str | None,
        typer.Option(
            "--separation-policy-bundle-id",
            help="A sealed separation-of-duty policy bundle (from `policy create-separation` "
            "+ `policy sign`) to enforce during this authorization",
        ),
    ] = None,
    role: Annotated[
        list[str],
        typer.Option(
            "--role",
            help="repeatable 'role_name:principal_id' (e.g. 'sealer:user:alice') -- "
            "additional roles beyond the auto-derived proposer/executor/approver",
        ),
    ] = [],  # noqa: B006
    decision_envelope_id: Annotated[
        str | None,
        typer.Option(
            "--decision-envelope-id",
            help="A sealed Decision Envelope (from `envelope create`) the sealed "
            "manifest must fit; bound into the grant and re-checked at execute",
        ),
    ] = None,
    causal_graph_id: Annotated[
        str | None,
        typer.Option(
            "--causal-graph-id",
            help="A sealed causal graph (from `graph create`) the sealed manifest "
            "must be a node of; bound into the grant and re-checked at execute. "
            "Mutually exclusive with --decision-envelope-id",
        ),
    ] = None,
    max_amount_minor: Annotated[
        int | None,
        typer.Option("--max-amount-minor", help="Cap the grant at this many minor units"),
    ] = None,
    currency: Annotated[str, typer.Option("--currency")] = "INR",
    allowed_recipient: Annotated[
        list[str],
        typer.Option("--allowed-recipient", help="Restrict recipients. Repeatable."),
    ] = [],  # noqa: B006
    effect_type: Annotated[
        list[str],
        typer.Option(
            "--effect-type",
            help="Allowed effect type. Repeatable. Defaults to the manifest effect type.",
        ),
    ] = [],  # noqa: B006
) -> None:
    """Issue an ExecutionGrant bound to a sealed manifest (invariant #30:
    issuer must be human or service, never the agent itself)."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        if decision_envelope_id is not None and causal_graph_id is not None:
            raise typer.BadParameter(
                "--decision-envelope-id and --causal-graph-id are mutually exclusive"
            )
        sealed = workspace.load_sealed_manifest(manifest_id)
        signing_key = workspace.load_signing_key(key_id)
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, manifest_id)
        now = datetime.now(timezone.utc)
        policy_bundle = (
            workspace.load_sealed_policy_bundle(policy_bundle_id)
            if policy_bundle_id is not None
            else None
        )
        separation_policy_bundle = (
            workspace.load_sealed_policy_bundle(separation_policy_bundle_id)
            if separation_policy_bundle_id is not None
            else None
        )
        role_assignment = _parse_role_assignment(sealed.seal.manifest_hash, role)
        issuer = Principal(principal_id=issuer_id, principal_type=issuer_type)
        subject = Principal(principal_id=subject_id, principal_type=subject_type)
        grant_audience = tuple(audience) or (sealed.manifest.adapter.adapter_id,)
        expires_at = now + timedelta(seconds=ttl_seconds)
        scope = _issue_scope(
            max_amount_minor=max_amount_minor,
            currency=currency,
            allowed_recipient=allowed_recipient,
        )
        effect_types = tuple(effect_type) if effect_type else (sealed.manifest.effect_type,)
        if decision_envelope_id is not None:
            envelope = workspace.load_decision_envelope(decision_envelope_id)
            grant = engine.authorize_with_envelope(
                sealed,
                envelope,
                issuer=issuer,
                subject=subject,
                audience=grant_audience,
                allowed_effect_types=effect_types,
                scope=scope,
                not_before=now,
                expires_at=expires_at,
                signing_key=signing_key,
                max_uses=max_uses,
                policy_bundle=policy_bundle,
                separation_policy_bundle=separation_policy_bundle,
                role_assignment=role_assignment,
            )
        elif causal_graph_id is not None:
            graph = workspace.load_causal_graph(causal_graph_id)
            grant = engine.authorize_plan(
                sealed,
                graph,
                issuer=issuer,
                subject=subject,
                audience=grant_audience,
                allowed_effect_types=effect_types,
                scope=scope,
                not_before=now,
                expires_at=expires_at,
                signing_key=signing_key,
                max_uses=max_uses,
                policy_bundle=policy_bundle,
                separation_policy_bundle=separation_policy_bundle,
                role_assignment=role_assignment,
            )
        else:
            grant = engine.authorize(
                sealed,
                issuer=issuer,
                subject=subject,
                audience=grant_audience,
                allowed_effect_types=effect_types,
                scope=scope,
                not_before=now,
                expires_at=expires_at,
                signing_key=signing_key,
                max_uses=max_uses,
                policy_bundle=policy_bundle,
                separation_policy_bundle=separation_policy_bundle,
                role_assignment=role_assignment,
            )
        path = workspace.save_grant(grant)
        emit(
            {
                "grant_id": grant.grant_id,
                "manifest_id": manifest_id,
                "policy_bundle_hash": grant.policy_bundle_hash,
                "decision_envelope_hash": grant.decision_envelope_hash,
                "causal_graph_hash": grant.causal_graph_hash,
                "path": str(path),
            },
            as_json=as_json,
            human=(
                f"Issued grant [bold]{grant.grant_id}[/bold] for manifest {manifest_id} -> {path}"
            ),
        )

    run_guarded(as_json, _do)


@grant_app.command("issue-with-quorum")
def issue_with_quorum(
    ctx: typer.Context,
    manifest_id: Annotated[str, typer.Argument()],
    approval_policy_bundle_id: Annotated[str, typer.Option("--approval-policy-bundle-id")],
    grant_issuer_id: Annotated[str, typer.Option()],
    proposer_id: Annotated[str, typer.Option()],
    subject_id: Annotated[str, typer.Option()],
    key_id: Annotated[str, typer.Option(help="Signing key id in the workspace")],
    grant_issuer_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.SERVICE,
    proposer_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.AGENT,
    subject_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.AGENT,
    audience: Annotated[
        list[str], typer.Option("--audience", help="Allowed adapter_id, repeatable")
    ] = [],  # noqa: B006
    max_uses: Annotated[int, typer.Option()] = 1,
    ttl_seconds: Annotated[int, typer.Option()] = 300,
    policy_bundle_id: Annotated[str | None, typer.Option("--policy-bundle-id")] = None,
    separation_policy_bundle_id: Annotated[
        str | None, typer.Option("--separation-policy-bundle-id")
    ] = None,
    role: Annotated[
        list[str],
        typer.Option(
            "--role",
            help="repeatable 'role_name:principal_id' -- additional roles beyond the "
            "auto-derived proposer/executor/approver(s)",
        ),
    ] = [],  # noqa: B006
) -> None:
    """Issue an ExecutionGrant only if the approval statements already
    submitted for this manifest (via `karmasakshi approve`) satisfy the
    quorum rules in the given signed approval policy bundle. Fails
    closed with a non-zero exit code (and no grant written) if quorum is
    not met -- see `karmasakshi approvals inspect` to check first."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        sealed = workspace.load_sealed_manifest(manifest_id)
        approval_bundle = workspace.load_sealed_policy_bundle(approval_policy_bundle_id)
        statements = workspace.load_approval_statements(sealed.seal.manifest_hash)
        signing_key = workspace.load_signing_key(key_id)
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, manifest_id)
        now = datetime.now(timezone.utc)
        policy_bundle = (
            workspace.load_sealed_policy_bundle(policy_bundle_id)
            if policy_bundle_id is not None
            else None
        )
        separation_policy_bundle = (
            workspace.load_sealed_policy_bundle(separation_policy_bundle_id)
            if separation_policy_bundle_id is not None
            else None
        )
        role_assignment = _parse_role_assignment(sealed.seal.manifest_hash, role)
        grant = engine.authorize_with_quorum(
            sealed,
            statements=statements,
            approval_policy_bundle=approval_bundle,
            proposer=Principal(principal_id=proposer_id, principal_type=proposer_type),
            subject=Principal(principal_id=subject_id, principal_type=subject_type),
            grant_issuer=Principal(principal_id=grant_issuer_id, principal_type=grant_issuer_type),
            audience=tuple(audience) or (sealed.manifest.adapter.adapter_id,),
            allowed_effect_types=(sealed.manifest.effect_type,),
            scope=ScopeConstraints(),
            not_before=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            signing_key=signing_key,
            max_uses=max_uses,
            policy_bundle=policy_bundle,
            separation_policy_bundle=separation_policy_bundle,
            role_assignment=role_assignment,
        )
        path = workspace.save_grant(grant)
        emit(
            {
                "grant_id": grant.grant_id,
                "manifest_id": manifest_id,
                "approval_set_hash": grant.approval_set_hash,
                "policy_bundle_hash": grant.policy_bundle_hash,
                "path": str(path),
            },
            as_json=as_json,
            human=(
                f"Issued grant [bold]{grant.grant_id}[/bold] (quorum met) "
                f"for manifest {manifest_id} -> {path}"
            ),
        )

    run_guarded(as_json, _do)


@grant_app.command("verify")
def verify(ctx: typer.Context, grant_id: Annotated[str, typer.Argument()]) -> None:
    """Verify a grant's signature and time-window validity."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        grant = workspace.load_grant(grant_id)
        keyring = workspace.load_keyring()
        verify_grant(grant, keyring, now=datetime.now(timezone.utc))
        emit(
            {"grant_id": grant_id, "valid": True},
            as_json=as_json,
            human=f"Grant [bold]{grant_id}[/bold] is valid.",
        )

    run_guarded(as_json, _do)


@grant_app.command("delegate")
def delegate(
    ctx: typer.Context,
    parent_grant_id: Annotated[str, typer.Argument()],
    issuer_id: Annotated[str, typer.Option()],
    subject_id: Annotated[str, typer.Option()],
    key_id: Annotated[str, typer.Option()],
    issuer_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.HUMAN,
    subject_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.AGENT,
    ttl_seconds: Annotated[
        int | None, typer.Option(help="Defaults to parent's remaining window")
    ] = None,
    max_uses: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Issue a child grant narrower-than-or-equal-to its parent (invariants #15-#18)."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        parent = workspace.load_grant(parent_grant_id)
        signing_key = workspace.load_signing_key(key_id)
        engine = workspace.build_engine()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        child = engine.delegate(
            parent,
            issuer=Principal(principal_id=issuer_id, principal_type=issuer_type),
            subject=Principal(principal_id=subject_id, principal_type=subject_type),
            signing_key=signing_key,
            grant_id=str(uuid.uuid4()),
            nonce=uuid.uuid4().hex,
            expires_at=expires_at,
            max_uses=max_uses,
        )
        path = workspace.save_grant(child)
        emit(
            {"grant_id": child.grant_id, "parent_grant_id": parent_grant_id, "path": str(path)},
            as_json=as_json,
            human=(
                f"Delegated child grant [bold]{child.grant_id}[/bold] "
                f"from {parent_grant_id} -> {path}"
            ),
        )

    run_guarded(as_json, _do)


@grant_app.command("revoke")
def revoke(
    ctx: typer.Context,
    grant_id: Annotated[str, typer.Argument()],
    manifest_id: Annotated[
        str,
        typer.Option(
            help="Manifest this grant is bound to (recommended: enables the "
            "safe-checkpoint lifecycle check; a grant only records its manifest's hash, not this "
            "id, so it cannot be recovered automatically)"
        ),
    ] = "",
) -> None:
    """Revoke a grant. Always prevents future use; only reports whether it
    stopped execution at a safe checkpoint if ``--manifest-id`` is given
    (invariant #27) -- without it, the grant is still revoked, but the
    lifecycle impact is reported as unknown rather than guessed."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        grant = workspace.load_grant(grant_id)
        engine = workspace.build_engine()
        if manifest_id:
            workspace.reconstruct_lifecycle_state(engine, manifest_id)
            stopped_at_safepoint: bool | None = engine.revoke(
                grant, manifest_id, revoked_by=grant.issuer
            )
        else:
            engine.context.grant_store.revoke(grant.grant_id)
            stopped_at_safepoint = None

        if stopped_at_safepoint is None:
            status_line = "Lifecycle impact unknown (pass --manifest-id to check)."
        elif stopped_at_safepoint:
            status_line = "Execution had not progressed past a safe checkpoint."
        else:
            status_line = (
                "Execution had already progressed past the safe checkpoint; "
                "any committed effect is unaffected."
            )
        emit(
            {"grant_id": grant_id, "stopped_at_safepoint": stopped_at_safepoint},
            as_json=as_json,
            human=f"Revoked grant [bold]{grant_id}[/bold]. {status_line}",
        )

    run_guarded(as_json, _do)


@grant_app.command("inspect")
def inspect(ctx: typer.Context, grant_id: Annotated[str, typer.Argument()]) -> None:
    """Print a grant's public fields (never its issuer's private key)."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        grant = workspace.load_grant(grant_id)
        data = grant.model_dump(mode="json")
        emit(data, as_json=as_json, human=grant.model_dump_json(indent=2))

    run_guarded(as_json, _do)


__all__ = ["grant_app"]

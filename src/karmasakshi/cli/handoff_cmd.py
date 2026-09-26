"""CLI for creating, accepting, and inspecting handoff envelopes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import typer

from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.domain.common import MonetaryAmount, Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.errors import HandoffRejectedError
from karmasakshi.grants.model import ScopeConstraints
from karmasakshi.handoff import (
    HandoffAcceptance,
    accept_handoff,
    create_handoff,
    export_workflow,
    open_workflow,
    workflow_from_export,
)

handoff_app = typer.Typer(help="Hand a narrowed grant from one agent to another.")


def _scope_from_parent(
    parent_scope: ScopeConstraints,
    *,
    max_amount_minor: int | None,
    currency: str,
    recipients: list[str],
) -> ScopeConstraints | None:
    if max_amount_minor is None and not recipients:
        return None
    updates: dict[str, object] = {}
    if max_amount_minor is not None:
        updates["max_amount"] = MonetaryAmount(currency=currency, minor_units=max_amount_minor)
    if recipients:
        updates["recipients"] = tuple(recipients)
    return parent_scope.model_copy(update=updates)


@handoff_app.command("create")
def create(
    ctx: typer.Context,
    parent_grant_id: Annotated[str, typer.Argument()],
    to_agent: Annotated[str, typer.Option("--to-agent")],
    task: Annotated[str, typer.Option("--task")],
    issuer_id: Annotated[str, typer.Option("--issuer-id")],
    key_id: Annotated[str, typer.Option("--key-id")],
    workflow_id: Annotated[str, typer.Option("--workflow-id")],
    issuer_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.HUMAN,
    from_agent: Annotated[
        str | None,
        typer.Option("--from-agent", help="Defaults to the parent grant's subject"),
    ] = None,
    effect_type: Annotated[
        list[str] | None,
        typer.Option("--effect-type", help="Narrow allowed effect types. Repeatable."),
    ] = None,
    recipient: Annotated[
        list[str] | None,
        typer.Option("--recipient", help="Narrow recipients. Repeatable."),
    ] = None,
    max_amount_minor: Annotated[int | None, typer.Option("--max-amount-minor")] = None,
    currency: Annotated[str, typer.Option()] = "INR",
    max_uses: Annotated[int | None, typer.Option()] = None,
    ttl_seconds: Annotated[int | None, typer.Option()] = None,
    manifest_id: Annotated[
        str | None,
        typer.Option("--manifest-id", help="Bind the child grant to this sealed manifest"),
    ] = None,
    handoff_id: Annotated[
        str | None,
        typer.Option("--handoff-id", help="Use this id instead of a generated UUID"),
    ] = None,
) -> None:
    """Delegate a narrower grant and write a handoff envelope.

    If ``--workflow-id`` does not exist yet, the parent grant must be a root
    and a workflow is opened from it.
    """
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        parent = workspace.load_grant(parent_grant_id)
        if handoff_id is not None and workspace.has_handoff(handoff_id):
            raise HandoffRejectedError(
                f"handoff id {handoff_id!r} already exists in this workspace"
            )
        signing_key = workspace.load_signing_key(key_id)
        engine = workspace.build_engine()
        if workspace.has_workflow(workflow_id):
            workflow = workflow_from_export(workspace.load_workflow_export(workflow_id))
        else:
            workflow = open_workflow(engine, parent, workflow_id=workflow_id)
        sender = None
        if from_agent is not None:
            sender = Principal(principal_id=from_agent, principal_type=PrincipalType.AGENT)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        manifest_hash = None
        if manifest_id is not None:
            manifest_hash = workspace.load_sealed_manifest(manifest_id).seal.manifest_hash
        envelope = create_handoff(
            engine,
            parent,
            Principal(principal_id=to_agent, principal_type=PrincipalType.AGENT),
            _scope_from_parent(
                parent.scope,
                max_amount_minor=max_amount_minor,
                currency=currency,
                recipients=recipient or [],
            ),
            workflow=workflow,
            issuer=Principal(principal_id=issuer_id, principal_type=issuer_type),
            signing_key=signing_key,
            task=task,
            from_agent=sender,
            allowed_effect_types=tuple(effect_type) if effect_type else None,
            max_uses=max_uses,
            expires_at=expires_at,
            manifest_hash=manifest_hash,
            handoff_id=handoff_id,
        )
        handoff_path = workspace.save_handoff(envelope)
        workspace.save_grant(envelope.grant)
        export = export_workflow(
            workflow, keyring=engine.context.keyring, clock=engine.context.clock
        )
        workflow_path = workspace.save_workflow_export(export)
        emit(
            {
                "handoff_id": envelope.handoff_id,
                "workflow_id": envelope.workflow_id,
                "grant_id": envelope.grant.grant_id,
                "parent_grant_id": envelope.parent_grant_id,
                "delegation_depth": envelope.delegation_depth,
                "content_hash": envelope.content_hash,
                "path": str(handoff_path),
                "workflow_path": str(workflow_path),
            },
            as_json=as_json,
            human=(
                f"Handoff [bold]{envelope.handoff_id}[/bold] "
                f"{envelope.from_agent.principal_id} -> {envelope.to_agent.principal_id} "
                f"(grant {envelope.grant.grant_id}) -> {handoff_path}"
            ),
        )

    run_guarded(as_json, _do)


@handoff_app.command("accept")
def accept(
    ctx: typer.Context,
    handoff_id: Annotated[str, typer.Argument()],
    agent_id: Annotated[str, typer.Option("--agent-id")],
) -> None:
    """Verify a handoff as the receiving agent. Fails closed on any problem."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        envelope = workspace.load_handoff(handoff_id)
        engine = workspace.build_engine()
        receiver = Principal(principal_id=agent_id, principal_type=PrincipalType.AGENT)
        accept_handoff(engine, envelope, receiver)
        workspace.save_handoff_acceptance(
            HandoffAcceptance(
                handoff_id=envelope.handoff_id,
                workflow_id=envelope.workflow_id,
                accepted_by=agent_id,
                accepted_at=engine.context.clock.now(),
                content_hash=envelope.content_hash,
            )
        )
        emit(
            {
                "handoff_id": envelope.handoff_id,
                "accepted_by": agent_id,
                "grant_id": envelope.grant.grant_id,
            },
            as_json=as_json,
            human=(
                f"Agent [bold]{agent_id}[/bold] accepted handoff {envelope.handoff_id} "
                f"(grant {envelope.grant.grant_id})"
            ),
        )

    run_guarded(as_json, _do)


@handoff_app.command("inspect")
def inspect(ctx: typer.Context, handoff_id: Annotated[str, typer.Argument()]) -> None:
    """Show a handoff envelope and whether its content hash still matches."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        envelope = workspace.load_handoff(handoff_id)
        intact = envelope.compute_content_hash() == envelope.content_hash
        emit(
            {
                "handoff_id": envelope.handoff_id,
                "workflow_id": envelope.workflow_id,
                "from_agent": envelope.from_agent.principal_id,
                "to_agent": envelope.to_agent.principal_id,
                "task": envelope.task,
                "grant_id": envelope.grant.grant_id,
                "parent_grant_id": envelope.parent_grant_id,
                "delegation_depth": envelope.delegation_depth,
                "created_at": envelope.created_at.isoformat(),
                "expires_at": envelope.expires_at.isoformat(),
                "content_hash": envelope.content_hash,
                "content_hash_ok": intact,
            },
            as_json=as_json,
            human=(
                f"{envelope.handoff_id}: {envelope.from_agent.principal_id} -> "
                f"{envelope.to_agent.principal_id} depth={envelope.delegation_depth} "
                f"hash_ok={intact}\n{envelope.task}"
            ),
        )

    run_guarded(as_json, _do)


__all__ = ["handoff_app"]

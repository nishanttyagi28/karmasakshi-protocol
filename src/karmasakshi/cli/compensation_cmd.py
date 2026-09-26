"""Authorized compensation path (Phase 7) and legacy compensate wrappers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import typer

from karmasakshi.cli.adapter_factory import build_adapter
from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.compensation import (
    build_compensation_manifest,
    build_compensation_passport,
)
from karmasakshi.domain.common import Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.grants.model import ScopeConstraints

compensation_app = typer.Typer(
    help="Prepare, authorize, and commit compensation as a separate effect."
)


@compensation_app.command("prepare")
def compensation_prepare(
    ctx: typer.Context,
    original_manifest_id: Annotated[str, typer.Argument()],
    key_id: Annotated[str, typer.Option()] = "issuer",
    ttl_seconds: Annotated[int, typer.Option()] = 300,
) -> None:
    """Build, register, and seal a compensation manifest bound to the original."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        original = workspace.load_sealed_manifest(original_manifest_id)
        commit_result = workspace.load_commit_result(original_manifest_id)
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, original_manifest_id)
        unsigned = build_compensation_manifest(
            original=original,
            original_commit=commit_result,
            ttl_seconds=ttl_seconds,
        )
        engine.prepare_compensation(unsigned, original_sealed=original)
        signing_key = workspace.load_signing_key(key_id)
        sealed = engine.seal(unsigned, signing_key)
        path = workspace.save_sealed_manifest(sealed)
        emit(
            {
                "compensation_manifest_id": sealed.manifest.manifest_id,
                "compensation_manifest_hash": sealed.seal.manifest_hash,
                "original_manifest_id": original.manifest.manifest_id,
                "original_manifest_hash": original.seal.manifest_hash,
                "path": str(path),
            },
            as_json=as_json,
            human=(
                f"Prepared sealed compensation [bold]{sealed.manifest.manifest_id}[/bold] "
                f"for original {original_manifest_id}"
            ),
        )

    run_guarded(as_json, _do)


@compensation_app.command("authorize")
def compensation_authorize(
    ctx: typer.Context,
    original_manifest_id: Annotated[str, typer.Argument()],
    compensation_manifest_id: Annotated[str, typer.Argument()],
    issuer_id: Annotated[str, typer.Option()] = "approver-1",
    issuer_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.HUMAN,
    subject_id: Annotated[str, typer.Option()] = "agent-1",
    subject_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.AGENT,
    key_id: Annotated[str, typer.Option()] = "issuer",
    ttl_seconds: Annotated[int, typer.Option()] = 300,
    audience: Annotated[list[str] | None, typer.Option("--audience")] = None,
) -> None:
    """Issue a grant for a sealed compensation manifest (separate from the original)."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        original = workspace.load_sealed_manifest(original_manifest_id)
        compensation = workspace.load_sealed_manifest(compensation_manifest_id)
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, original_manifest_id)
        workspace.reconstruct_lifecycle_state(engine, compensation_manifest_id)
        now = datetime.now(timezone.utc)
        signing_key = workspace.load_signing_key(key_id)
        grant = engine.authorize_compensation(
            original,
            compensation,
            issuer=Principal(principal_id=issuer_id, principal_type=issuer_type),
            subject=Principal(principal_id=subject_id, principal_type=subject_type),
            audience=tuple(audience) if audience else (compensation.manifest.adapter.adapter_id,),
            allowed_effect_types=(compensation.manifest.effect_type,),
            scope=ScopeConstraints(),
            not_before=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            signing_key=signing_key,
        )
        path = workspace.save_grant(grant)
        emit(
            {
                "grant_id": grant.grant_id,
                "manifest_hash": grant.manifest_hash,
                "original_manifest_id": original_manifest_id,
                "compensation_manifest_id": compensation_manifest_id,
                "path": str(path),
            },
            as_json=as_json,
            human=(
                f"Authorized compensation grant [bold]{grant.grant_id}[/bold] "
                f"for {compensation_manifest_id}"
            ),
        )

    run_guarded(as_json, _do)


@compensation_app.command("execute")
def compensation_execute(
    ctx: typer.Context,
    original_manifest_id: Annotated[str, typer.Argument()],
    compensation_manifest_id: Annotated[str, typer.Argument()],
    grant_id: Annotated[str, typer.Option()],
    adapter: Annotated[str, typer.Option("--adapter", help="sqlite, email, or payment")],
    sqlite_db_path: Annotated[str | None, typer.Option()] = None,
    sqlite_table: Annotated[str, typer.Option()] = "ledger_accounts",
    fund_source_account: Annotated[int | None, typer.Option()] = None,
    fund_account_id: Annotated[str, typer.Option()] = "acct-src",
) -> None:
    """Commit an authorized compensation effect; never mutates Action Passports."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        original = workspace.load_sealed_manifest(original_manifest_id)
        compensation = workspace.load_sealed_manifest(compensation_manifest_id)
        grant = workspace.load_grant(grant_id)
        payment_simulator = workspace.load_payment_simulator() if adapter == "payment" else None
        adapter_instance = build_adapter(
            adapter,
            sqlite_db_path=sqlite_db_path,
            sqlite_table=sqlite_table,
            fund_source_account=fund_source_account,
            fund_account_id=fund_account_id,
            payment_simulator=payment_simulator,
        )
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, original_manifest_id)
        workspace.reconstruct_lifecycle_state(engine, compensation_manifest_id)
        original_commit = workspace.load_commit_result(original_manifest_id)
        if original_commit is None:
            raise ValueError(
                f"no commit result for original {original_manifest_id!r}; run execute first"
            )
        result = engine.commit_compensation(
            original,
            compensation,
            grant,
            adapter_instance,
            context=None,
            original_commit=original_commit,
        )
        workspace.save_commit_result(compensation_manifest_id, result)
        if payment_simulator is not None:
            workspace.save_payment_simulator(payment_simulator)
        emit(
            {
                "original_manifest_id": original_manifest_id,
                "compensation_manifest_id": compensation_manifest_id,
                "success": result.success,
                "provider_reference": result.provider_reference,
                "detail": result.detail,
            },
            as_json=as_json,
            human=(
                f"Compensation commit {'succeeded' if result.success else 'failed'} "
                f"for [bold]{compensation_manifest_id}[/bold]"
            ),
        )

    run_guarded(as_json, _do)


@compensation_app.command("passport")
def compensation_passport_cmd(
    ctx: typer.Context,
    original_manifest_id: Annotated[str, typer.Argument()],
    compensation_manifest_id: Annotated[str, typer.Argument()],
    grant_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Emit a Compensation Passport (separate from the Action Passport)."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        original = workspace.load_sealed_manifest(original_manifest_id)
        compensation = workspace.load_sealed_manifest(compensation_manifest_id)
        grant = workspace.load_grant(grant_id) if grant_id else None
        commit_result = workspace.load_commit_result(compensation_manifest_id)
        outcome_proof = workspace.load_outcome_proof(compensation_manifest_id)
        engine = workspace.build_engine()
        passport = build_compensation_passport(
            compensation_sealed=compensation,
            original_sealed=original,
            keyring=workspace.load_keyring(),
            audit=engine.context.audit,
            grant=grant,
            commit_result=commit_result,
            outcome_proof=outcome_proof,
        )
        path = workspace.save_compensation_passport(passport)
        emit(
            passport.model_dump(mode="json") | {"path": str(path)},
            as_json=as_json,
            human=(f"Compensation Passport status=[bold]{passport.status.value}[/bold] -> {path}"),
        )

    run_guarded(as_json, _do)


__all__ = ["compensation_app"]

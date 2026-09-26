from __future__ import annotations

import uuid
from typing import Annotated

import typer

from karmasakshi.adapters.payment_simulator import PaymentSimulatorAdapter
from karmasakshi.cli.adapter_factory import build_adapter, build_request
from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.domain.common import Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.state_machine.states import LifecycleState


def prepare(
    ctx: typer.Context,
    adapter: Annotated[str, typer.Option("--adapter", help="sqlite, email, or payment")],
    actor_id: Annotated[str, typer.Option()],
    principal_id: Annotated[str, typer.Option()] = "",
    actor_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.AGENT,
    principal_type: Annotated[PrincipalType, typer.Option()] = PrincipalType.HUMAN,
    idempotency_key: Annotated[str, typer.Option()] = "",
    ttl_seconds: Annotated[int, typer.Option()] = 300,
    # sqlite
    sqlite_db_path: Annotated[str | None, typer.Option()] = None,
    sqlite_table: Annotated[str, typer.Option()] = "ledger_accounts",
    row_operation: Annotated[str | None, typer.Option("--row-operation")] = None,
    row_id: Annotated[str | None, typer.Option()] = None,
    new_balance: Annotated[int | None, typer.Option()] = None,
    # email
    recipient: Annotated[list[str], typer.Option("--recipient", help="repeatable")] = [],  # noqa: B006
    subject: Annotated[str | None, typer.Option()] = None,
    body: Annotated[str | None, typer.Option()] = None,
    # payment
    source_account: Annotated[str | None, typer.Option()] = None,
    beneficiary: Annotated[str | None, typer.Option()] = None,
    amount_minor_units: Annotated[int | None, typer.Option()] = None,
    currency: Annotated[str, typer.Option()] = "INR",
    reference: Annotated[str | None, typer.Option()] = None,
    fee_minor_units: Annotated[int, typer.Option()] = 0,
    fund_source_account: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Resolve a request into an EffectManifest via a real reference adapter
    and save it to the workspace, ready for seal/authorize/execute."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        workspace.ensure_initialized()
        payment_simulator = workspace.load_payment_simulator() if adapter == "payment" else None
        adapter_instance = build_adapter(
            adapter,
            sqlite_db_path=sqlite_db_path,
            sqlite_table=sqlite_table,
            fund_source_account=fund_source_account,
            fund_account_id=source_account or "acct-src",
            payment_simulator=payment_simulator,
        )
        request = build_request(
            adapter,
            actor=Principal(principal_id=actor_id, principal_type=actor_type),
            principal=Principal(
                principal_id=principal_id or actor_id, principal_type=principal_type
            ),
            idempotency_key=idempotency_key or f"idem-{uuid.uuid4().hex[:12]}",
            ttl_seconds=ttl_seconds,
            row_operation=row_operation,  # type: ignore[arg-type]
            row_id=row_id,
            new_balance=new_balance,
            recipients=recipient,
            subject=subject,
            body=body,
            source_account=source_account,
            beneficiary=beneficiary,
            amount_minor_units=amount_minor_units,
            currency=currency,
            reference=reference,
            fee_minor_units=fee_minor_units,
        )
        engine = workspace.build_engine()
        manifest = engine.prepare(adapter_instance, request, context=None)
        if isinstance(adapter_instance, PaymentSimulatorAdapter):
            workspace.save_payment_simulator(adapter_instance.simulator)
        path = workspace.save_unsealed_manifest(manifest)
        emit(
            {"manifest_id": manifest.manifest_id, "path": str(path)},
            as_json=as_json,
            human=f"Prepared manifest [bold]{manifest.manifest_id}[/bold] -> {path}",
        )

    run_guarded(as_json, _do)


def seal(
    ctx: typer.Context,
    manifest_id: Annotated[str, typer.Argument()],
    key_id: Annotated[str, typer.Option(help="Signing key id in the workspace")],
) -> None:
    """Sign a prepared manifest's canonical hash, producing a sealed manifest."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        manifest = workspace.load_unsealed_manifest(manifest_id)
        signing_key = workspace.load_signing_key(key_id)
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, manifest_id)
        if engine.get_lifecycle_state(manifest_id) == LifecycleState.PROPOSED:
            engine.seed_lifecycle_state(manifest_id, LifecycleState.PREPARED)
        sealed = engine.seal(manifest, signing_key)
        path = workspace.save_sealed_manifest(sealed)
        emit(
            {
                "manifest_id": manifest.manifest_id,
                "manifest_hash": sealed.seal.manifest_hash,
                "path": str(path),
            },
            as_json=as_json,
            human=(
                f"Sealed [bold]{manifest.manifest_id}[/bold]: {sealed.seal.manifest_hash} -> {path}"
            ),
        )

    run_guarded(as_json, _do)


__all__ = ["prepare", "seal"]

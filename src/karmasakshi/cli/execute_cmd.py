from __future__ import annotations

from typing import Annotated

import typer

from karmasakshi.adapters.payment_simulator import PaymentSimulator
from karmasakshi.cli.adapter_factory import build_adapter
from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.domain.seal import SealedManifest
from karmasakshi.errors import HandoffRejectedError
from karmasakshi.handoff import (
    assert_handoff_ready_for_execute,
    export_workflow,
    record_effect,
    workflow_from_export,
)


def execute(
    ctx: typer.Context,
    manifest_id: Annotated[str, typer.Argument()],
    grant_id: Annotated[str, typer.Option()],
    adapter: Annotated[str, typer.Option("--adapter", help="sqlite, email, or payment")],
    sqlite_db_path: Annotated[str | None, typer.Option()] = None,
    sqlite_table: Annotated[str, typer.Option()] = "ledger_accounts",
    fund_source_account: Annotated[int | None, typer.Option()] = None,
    fund_account_id: Annotated[
        str | None,
        typer.Option(
            "--fund-account-id",
            help="Account to credit when --fund-source-account is set. "
            "Must be the manifest's source account. Omit to use that account.",
        ),
    ] = None,
    policy_bundle_id: Annotated[
        str | None,
        typer.Option(
            "--policy-bundle-id",
            help="Required if the grant was issued with a policy bundle bound to it; "
            "must be the same bundle (by hash) presented at `grant issue` time",
        ),
    ] = None,
    decision_envelope_id: Annotated[
        str | None,
        typer.Option(
            "--decision-envelope-id",
            help="Required if the grant was issued with a Decision Envelope bound; "
            "must be the same envelope (by hash) presented at `grant issue` time",
        ),
    ] = None,
    causal_graph_id: Annotated[
        str | None,
        typer.Option(
            "--causal-graph-id",
            help="Required if the grant was issued via atomic plan authorization; "
            "must be the same graph (by hash) presented at `grant issue` time",
        ),
    ] = None,
    workflow_id: Annotated[
        str | None,
        typer.Option(
            "--workflow-id",
            help="Record the passport and evidence pack on this workflow after verify",
        ),
    ] = None,
    handoff_id: Annotated[
        str | None,
        typer.Option(
            "--handoff-id",
            help="Accepted handoff whose delegated grant is --grant-id",
        ),
    ] = None,
) -> None:
    """Commit a sealed, authorized manifest through its adapter."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        sealed = workspace.load_sealed_manifest(manifest_id)
        grant = workspace.load_grant(grant_id)
        if (workflow_id is None) != (handoff_id is None):
            raise HandoffRejectedError("--workflow-id and --handoff-id must be given together")
        engine = workspace.build_engine()
        if workflow_id is not None and handoff_id is not None:
            envelope = workspace.load_handoff(handoff_id)
            if envelope.workflow_id != workflow_id:
                raise HandoffRejectedError(
                    f"handoff {handoff_id} belongs to workflow {envelope.workflow_id}, "
                    f"not {workflow_id}"
                )
            if not workspace.has_workflow(workflow_id):
                raise HandoffRejectedError(f"no workflow {workflow_id} in this workspace")
            acceptance = workspace.load_handoff_acceptance(handoff_id)
            if acceptance is None:
                raise HandoffRejectedError(
                    f"handoff {handoff_id} has not been accepted by the executing agent"
                )
            assert_handoff_ready_for_execute(
                engine,
                envelope,
                grant,
                acceptance,
                workflow_id=workflow_id,
            )
        payment_simulator = None
        if adapter == "payment":
            payment_simulator = workspace.load_payment_simulator()
            _fund_manifest_source(
                payment_simulator,
                sealed,
                fund_source_account=fund_source_account,
                fund_account_id=fund_account_id,
            )
        adapter_instance = build_adapter(
            adapter,
            sqlite_db_path=sqlite_db_path,
            sqlite_table=sqlite_table,
            fund_source_account=None,
            payment_simulator=payment_simulator,
        )
        workspace.reconstruct_lifecycle_state(engine, manifest_id)
        policy_bundle = (
            workspace.load_sealed_policy_bundle(policy_bundle_id)
            if policy_bundle_id is not None
            else None
        )
        decision_envelope = (
            workspace.load_decision_envelope(decision_envelope_id)
            if decision_envelope_id is not None
            else None
        )
        causal_graph = (
            workspace.load_causal_graph(causal_graph_id) if causal_graph_id is not None else None
        )
        result = engine.commit(
            sealed,
            grant,
            adapter_instance,
            context=None,
            policy_bundle=policy_bundle,
            decision_envelope=decision_envelope,
            causal_graph=causal_graph,
        )
        workspace.save_commit_result(manifest_id, result)
        if payment_simulator is not None:
            workspace.save_payment_simulator(payment_simulator)
        recorded_workflow = None
        if workflow_id is not None:
            if not result.success:
                raise HandoffRejectedError(
                    f"commit failed; passport not recorded: {result.detail or 'unsuccessful'}"
                )
            proof = engine.verify(sealed.manifest, result, adapter_instance, context=None)
            workspace.save_outcome_proof(manifest_id, proof)
            if not proof.matched_expected:
                raise HandoffRejectedError(
                    f"verification mismatch; passport not recorded: {proof.detail}"
                )
            workflow = workflow_from_export(workspace.load_workflow_export(workflow_id))
            record_effect(
                workflow,
                engine=engine,
                sealed=sealed,
                grant=grant,
                commit_result=result,
                outcome_proof=proof,
            )
            recorded_workflow = export_workflow(
                workflow, keyring=engine.context.keyring, clock=engine.context.clock
            )
            workspace.save_workflow_export(recorded_workflow)
        payload = {
            "manifest_id": manifest_id,
            "success": result.success,
            "provider_reference": result.provider_reference,
            "detail": result.detail,
        }
        human = (
            f"Commit {'succeeded' if result.success else 'failed'} for "
            f"[bold]{manifest_id}[/bold]: {result.detail or result.provider_reference or ''}"
        )
        if recorded_workflow is not None:
            payload["workflow_id"] = recorded_workflow.workflow_id
            payload["passports"] = len(recorded_workflow.passports)
            payload["evidence_packs"] = len(recorded_workflow.evidence_packs)
            human += (
                f"\nRecorded passport and evidence pack on workflow "
                f"[bold]{recorded_workflow.workflow_id}[/bold]"
            )
        emit(payload, as_json=as_json, human=human)

    run_guarded(as_json, _do)


def verify(
    ctx: typer.Context,
    manifest_id: Annotated[str, typer.Argument()],
    adapter: Annotated[str, typer.Option("--adapter", help="sqlite, email, or payment")],
    sqlite_db_path: Annotated[str | None, typer.Option()] = None,
    sqlite_table: Annotated[str, typer.Option()] = "ledger_accounts",
) -> None:
    """Independently verify the observed outcome of a committed manifest."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        sealed = workspace.load_sealed_manifest(manifest_id)
        commit_result = workspace.load_commit_result(manifest_id)
        if commit_result is None:
            raise ValueError(
                f"no commit result found for manifest_id={manifest_id!r}; run execute first"
            )
        payment_simulator = workspace.load_payment_simulator() if adapter == "payment" else None
        adapter_instance = build_adapter(
            adapter,
            sqlite_db_path=sqlite_db_path,
            sqlite_table=sqlite_table,
            fund_source_account=None,
            payment_simulator=payment_simulator,
        )
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, manifest_id)
        proof = engine.verify(sealed.manifest, commit_result, adapter_instance, context=None)
        workspace.save_outcome_proof(manifest_id, proof)
        emit(
            {
                "manifest_id": manifest_id,
                "matched_expected": proof.matched_expected,
                "detail": proof.detail,
            },
            as_json=as_json,
            human=(
                f"Verification for [bold]{manifest_id}[/bold]: "
                f"{'matched expected outcome' if proof.matched_expected else 'MISMATCH'}"
            ),
        )

    run_guarded(as_json, _do)


def compensate(
    ctx: typer.Context,
    manifest_id: Annotated[str, typer.Argument()],
    adapter: Annotated[str, typer.Option("--adapter", help="sqlite, email, or payment")],
    sqlite_db_path: Annotated[str | None, typer.Option()] = None,
    sqlite_table: Annotated[str, typer.Option()] = "ledger_accounts",
) -> None:
    """Attempt best-effort compensation of a committed manifest."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        sealed = workspace.load_sealed_manifest(manifest_id)
        commit_result = workspace.load_commit_result(manifest_id)
        if commit_result is None:
            raise ValueError(
                f"no commit result found for manifest_id={manifest_id!r}; run execute first"
            )
        payment_simulator = workspace.load_payment_simulator() if adapter == "payment" else None
        adapter_instance = build_adapter(
            adapter,
            sqlite_db_path=sqlite_db_path,
            sqlite_table=sqlite_table,
            fund_source_account=None,
            payment_simulator=payment_simulator,
        )
        engine = workspace.build_engine()
        workspace.reconstruct_lifecycle_state(engine, manifest_id)
        result = engine.compensate(sealed.manifest, commit_result, adapter_instance, context=None)
        workspace.save_compensation_result(manifest_id, result)
        if payment_simulator is not None:
            workspace.save_payment_simulator(payment_simulator)
        emit(
            {
                "manifest_id": manifest_id,
                "attempted": result.attempted,
                "succeeded": result.succeeded,
                "reason": result.reason,
            },
            as_json=as_json,
            human=(
                f"Compensation for [bold]{manifest_id}[/bold]: "
                f"attempted={result.attempted}, succeeded={result.succeeded}"
                + (f" ({result.reason})" if result.reason else "")
            ),
        )

    run_guarded(as_json, _do)


def _manifest_source_account(sealed: SealedManifest) -> str:
    source = sealed.manifest.parameters.get("source_account")
    if not isinstance(source, str) or not source:
        raise ValueError("payment manifest has no source_account parameter")
    return source


def _fund_manifest_source(
    simulator: PaymentSimulator,
    sealed: SealedManifest,
    *,
    fund_source_account: int | None,
    fund_account_id: str | None,
) -> None:
    """Credit the sealed source account, not a hardcoded ``acct-src``.

    ``prepare`` already fingerprints the balance and, when asked, writes
    that balance into the workspace snapshot. Funding again on top of a
    matching snapshot would change the balance and fail the precondition,
    which leaves the manifest failed. If the snapshot is empty (no prior
    prepare in this workspace), apply ``--fund-source-account`` to the
    manifest's own source account.
    """
    if fund_source_account is None and fund_account_id is None:
        return
    source = _manifest_source_account(sealed)
    if fund_account_id is not None and fund_account_id != source:
        raise ValueError(
            f"--fund-account-id {fund_account_id!r} does not match the manifest "
            f"source account {source!r}; refusing to fund a different account"
        )
    if fund_source_account is None:
        return
    fingerprint = sealed.manifest.state_fingerprint
    if fingerprint is not None and fingerprint.value.startswith("balance:"):
        expected = int(fingerprint.value.removeprefix("balance:"))
        if simulator.get_balance(source) == expected:
            return
    simulator.fund_account(source, fund_source_account)


__all__ = ["compensate", "execute", "verify"]

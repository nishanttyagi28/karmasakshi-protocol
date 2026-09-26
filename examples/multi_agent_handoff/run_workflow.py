"""Three stub agents, one human grant, one verified payment.

No model calls. The planner, researcher, and executor are fixed functions.
The signing key stays in the graph builder's closure, same as
``karmasakshi.integrations.langgraph``: it is never written into graph state.

The stock LangGraph helper commits the grant it just authorized. This graph
stops after the human authorizes the planner, then hands narrower grants to
the other two agents before anything is committed.

Run from the repo root:

    python examples/multi_agent_handoff/run_workflow.py
    karmasakshi workflow export wf-refund-8842 --workspace .karmasakshi
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from karmasakshi.adapters.payment_simulator import (
    PaymentRequest,
    PaymentSimulator,
    PaymentSimulatorAdapter,
)
from karmasakshi.audit.journal import AuditJournal
from karmasakshi.cli.workspace import Workspace
from karmasakshi.crypto import Keyring, SigningKey, generate_signing_key
from karmasakshi.domain.common import MonetaryAmount, Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.engine import EngineContext, KarmaSakshiEngine
from karmasakshi.errors import KarmaSakshiError
from karmasakshi.grants.model import ScopeConstraints
from karmasakshi.handoff import (
    WorkflowExport,
    WorkflowRecord,
    accept_handoff,
    create_handoff,
    export_workflow,
    open_workflow,
    record_effect,
    verify_workflow_export,
)
from karmasakshi.stores.memory import InMemoryGrantStore

WORKFLOW_ID = "wf-refund-8842"
PARENT_CAP_MINOR = 500_000
TRANSFER_MINOR = 150_000
CURRENCY = "INR"

HUMAN = Principal(
    principal_id="finance-approver",
    principal_type=PrincipalType.HUMAN,
    display_name="Finance approver",
)
PLANNER = Principal(
    principal_id="planner", principal_type=PrincipalType.AGENT, display_name="Planner"
)
RESEARCHER = Principal(
    principal_id="researcher", principal_type=PrincipalType.AGENT, display_name="Researcher"
)
EXECUTOR = Principal(
    principal_id="executor", principal_type=PrincipalType.AGENT, display_name="Executor"
)


class HandoffGraphState(TypedDict, total=False):
    status: str
    manifest_hash: str | None
    denial_reason: str | None
    passport_status: str | None
    evidence_verified: bool | None


def planner_tasks() -> tuple[str, str]:
    """Deterministic stand-in for a planner model."""
    return (
        "Read the refund beneficiary and amount. Do not transfer.",
        "Transfer exactly 150000 INR minor units to priya for order 8842.",
    )


def _request() -> PaymentRequest:
    return PaymentRequest(
        actor=PLANNER,
        principal=HUMAN,
        source_account="operating",
        beneficiary="priya",
        amount_minor_units=TRANSFER_MINOR,
        currency=CURRENCY,
        reference="order-8842",
        idempotency_key="refund-priya-8842",
    )


def _root_scope() -> ScopeConstraints:
    return ScopeConstraints(
        recipients=("priya",),
        max_amount=MonetaryAmount(currency=CURRENCY, minor_units=PARENT_CAP_MINOR),
    )


def build_multi_agent_handoff_graph(
    *,
    engine: KarmaSakshiEngine,
    adapter: PaymentSimulatorAdapter,
    signing_key: SigningKey,
    holder: dict[str, Any],
) -> Any:
    """Compile the planner -> researcher -> executor graph.

    ``holder`` keeps the workflow and the export outside graph state so a
    checkpoint cannot contain the signing key or a private grant signature
    blob beyond what the engine already returns to callers. The key itself
    is only closed over here.
    """

    def prepare_node(state: HandoffGraphState) -> dict[str, Any]:
        del state
        manifest = engine.prepare(adapter, _request(), None)
        sealed = engine.seal(manifest, signing_key)
        holder["sealed"] = sealed
        return {"manifest_hash": sealed.seal.manifest_hash, "status": "sealed"}

    def authorize_node(state: HandoffGraphState) -> dict[str, Any]:
        sealed = holder["sealed"]
        decision = interrupt(
            {
                "action": "authorize_root_grant",
                "manifest_hash": state.get("manifest_hash"),
                "subject": PLANNER.principal_id,
                "max_amount_minor": PARENT_CAP_MINOR,
                "currency": CURRENCY,
            }
        )
        if not decision.get("approved"):
            return {
                "status": "denied",
                "denial_reason": decision.get("reason", "denied by authorizer"),
            }
        now = datetime.now(timezone.utc)
        try:
            grant = engine.authorize(
                sealed,
                issuer=Principal(**decision["issuer"]),
                subject=PLANNER,
                audience=(adapter.adapter_id,),
                allowed_effect_types=("payment.read", "payment.transfer"),
                scope=_root_scope(),
                not_before=now,
                expires_at=now + timedelta(minutes=10),
                signing_key=signing_key,
                max_uses=2,
                grant_id="root-planner",
            )
        except KarmaSakshiError as exc:
            return {"status": "authorization_failed", "denial_reason": str(exc)}
        holder["workflow"] = open_workflow(engine, grant, workflow_id=WORKFLOW_ID)
        return {"status": "authorized"}

    def planner_node(state: HandoffGraphState) -> dict[str, Any]:
        if state.get("status") != "authorized":
            return {}
        workflow: WorkflowRecord = holder["workflow"]
        root = workflow.root_grant
        sealed = holder["sealed"]
        read_task, write_task = planner_tasks()
        researcher_env = create_handoff(
            engine,
            root,
            RESEARCHER,
            root.scope.model_copy(
                update={"max_amount": MonetaryAmount(currency=CURRENCY, minor_units=0)}
            ),
            workflow=workflow,
            issuer=HUMAN,
            signing_key=signing_key,
            task=read_task,
            from_agent=PLANNER,
            allowed_effect_types=("payment.read",),
            max_uses=1,
            grant_id="grant-researcher",
            handoff_id="handoff-researcher",
        )
        executor_env = create_handoff(
            engine,
            root,
            EXECUTOR,
            root.scope.model_copy(
                update={"max_amount": MonetaryAmount(currency=CURRENCY, minor_units=TRANSFER_MINOR)}
            ),
            workflow=workflow,
            issuer=HUMAN,
            signing_key=signing_key,
            task=write_task,
            from_agent=PLANNER,
            allowed_effect_types=("payment.transfer",),
            max_uses=1,
            manifest_hash=sealed.seal.manifest_hash,
            grant_id="grant-executor",
            handoff_id="handoff-executor",
        )
        holder["researcher_env"] = researcher_env
        holder["executor_env"] = executor_env
        return {"status": "planned"}

    def researcher_node(state: HandoffGraphState) -> dict[str, Any]:
        if state.get("status") != "planned":
            return {}
        accept_handoff(engine, holder["researcher_env"], RESEARCHER)
        return {"status": "researched"}

    def executor_node(state: HandoffGraphState) -> dict[str, Any]:
        if state.get("status") != "researched":
            return {}
        workflow = holder["workflow"]
        sealed = holder["sealed"]
        accepted = accept_handoff(engine, holder["executor_env"], EXECUTOR)
        try:
            result = engine.commit(sealed, accepted.grant, adapter, None)
        except KarmaSakshiError as exc:
            return {"status": "commit_failed", "denial_reason": str(exc)}
        if not result.success:
            return {"status": "commit_failed", "denial_reason": result.detail}
        proof = engine.verify(sealed.manifest, result, adapter, None)
        record_effect(
            workflow,
            engine=engine,
            sealed=sealed,
            grant=accepted.grant,
            commit_result=result,
            outcome_proof=proof,
        )
        export = export_workflow(
            workflow, keyring=engine.context.keyring, clock=engine.context.clock
        )
        holder["export"] = export
        passport_status = export.passports[-1].outcome_status.value
        return {
            "status": "verified" if proof.matched_expected else "verification_mismatch",
            "passport_status": passport_status,
            "evidence_verified": verify_workflow_export(export).all_verified,
        }

    graph: StateGraph[HandoffGraphState, Any, Any, Any] = StateGraph(HandoffGraphState)
    graph.add_node("prepare", prepare_node)
    graph.add_node("authorize", authorize_node)
    graph.add_node("planner", planner_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("executor", executor_node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "authorize")
    graph.add_edge("authorize", "planner")
    graph.add_edge("planner", "researcher")
    graph.add_edge("researcher", "executor")
    graph.add_edge("executor", END)
    return graph.compile(checkpointer=MemorySaver())


def build_demo_runtime() -> tuple[
    KarmaSakshiEngine, PaymentSimulatorAdapter, SigningKey, PaymentSimulator
]:
    signing_key = generate_signing_key("demo-issuer")
    simulator = PaymentSimulator()
    simulator.fund_account("operating", 1_000_000)
    adapter = PaymentSimulatorAdapter(simulator)
    engine = KarmaSakshiEngine(
        EngineContext(
            keyring=Keyring([signing_key.verification_key()]),
            grant_store=InMemoryGrantStore(),
            audit=AuditJournal(),
        )
    )
    return engine, adapter, signing_key, simulator


def run_demo() -> dict[str, Any]:
    """Run the graph, including the human resume, and return the export."""
    engine, adapter, signing_key, simulator = build_demo_runtime()
    holder: dict[str, Any] = {}
    app = build_multi_agent_handoff_graph(
        engine=engine, adapter=adapter, signing_key=signing_key, holder=holder
    )
    config = {"configurable": {"thread_id": WORKFLOW_ID}}
    paused = app.invoke({}, config)
    finished = app.invoke(
        Command(
            resume={
                "approved": True,
                "issuer": {
                    "principal_id": HUMAN.principal_id,
                    "principal_type": HUMAN.principal_type.value,
                },
            }
        ),
        config,
    )
    export = holder["export"]
    verification = verify_workflow_export(export)
    return {
        "paused_status": paused.get("status"),
        "status": finished.get("status"),
        "passport_status": finished.get("passport_status"),
        "evidence_verified": verification.all_verified,
        "export": export,
        "verification": verification,
        "balance": simulator.get_balance("operating"),
        "holder": holder,
        "app": app,
        "config": config,
    }


def persist_workflow(export: WorkflowExport, workspace: Path) -> Path:
    """Write the export where ``karmasakshi workflow export`` can read it."""
    ws = Workspace(workspace)
    ws.ensure_initialized()
    for envelope in export.handoffs:
        ws.save_handoff(envelope)
    return ws.save_workflow_export(export)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the three-agent handoff example.")
    parser.add_argument(
        "--workspace",
        default=".karmasakshi",
        help="Directory to write the workflow export into (default: ./.karmasakshi)",
    )
    args = parser.parse_args(argv)
    result = run_demo()
    export = result["export"]
    saved = persist_workflow(export, Path(args.workspace))
    print(f"status: {result['status']}")
    print(f"passport: {result['passport_status']}")
    print(f"evidence_verified: {result['evidence_verified']}")
    print(f"workflow: {export.workflow_id}")
    print(f"handoffs: {len(export.handoffs)}")
    print(f"operating balance: {result['balance']}")
    print(f"saved: {saved}")
    print(f"karmasakshi workflow export {export.workflow_id} --workspace {args.workspace}")
    if not result["evidence_verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

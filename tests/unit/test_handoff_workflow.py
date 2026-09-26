"""A three-agent workflow ends in a passport and an evidence pack."""

from __future__ import annotations

from datetime import timedelta

from karmasakshi.domain.common import MonetaryAmount, Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.grants.model import ScopeConstraints
from karmasakshi.handoff import (
    accept_handoff,
    create_handoff,
    export_workflow,
    open_workflow,
    record_effect,
    verify_workflow_export,
)
from karmasakshi.passports.v2 import OutcomeStatus
from karmasakshi.portable import verify_evidence_pack


def _agent(principal_id: str) -> Principal:
    return Principal(principal_id=principal_id, principal_type=PrincipalType.AGENT)


def _run_workflow(
    engine, fake_adapter, manifest_factory, human, signing_key, now, *, workflow_id, manifest_id
):
    planner = _agent("planner")
    researcher = _agent("researcher")
    executor = _agent("executor")
    prepared = engine.prepare(
        fake_adapter,
        manifest_factory(
            manifest_id=manifest_id,
            effect_type="payment.transfer",
            target_resource="payment:beneficiary/priya",
            parameters={"amount": 150000, "currency": "INR", "beneficiary": "priya"},
            idempotency_key=f"idem-{manifest_id}",
            nonce=f"nonce-{manifest_id}",
        ),
        context=None,
    )
    sealed = engine.seal(prepared, signing_key)
    root = engine.authorize(
        sealed,
        issuer=human,
        subject=planner,
        audience=("payment.simulator",),
        allowed_effect_types=("payment.read", "payment.transfer"),
        scope=ScopeConstraints(
            recipients=("priya",),
            max_amount=MonetaryAmount(currency="INR", minor_units=500_000),
        ),
        not_before=now,
        expires_at=now + timedelta(hours=1),
        signing_key=signing_key,
        max_uses=2,
        grant_id=f"root-{workflow_id}",
    )
    workflow = open_workflow(engine, root, workflow_id=workflow_id)
    researcher_env = create_handoff(
        engine,
        root,
        researcher,
        ScopeConstraints(
            recipients=("priya",),
            max_amount=MonetaryAmount(currency="INR", minor_units=0),
        ),
        workflow=workflow,
        issuer=human,
        signing_key=signing_key,
        task="Read the beneficiary. Do not transfer.",
        from_agent=planner,
        allowed_effect_types=("payment.read",),
        max_uses=1,
        grant_id=f"researcher-{workflow_id}",
        handoff_id=f"handoff-researcher-{workflow_id}",
    )
    executor_env = create_handoff(
        engine,
        root,
        executor,
        ScopeConstraints(
            recipients=("priya",),
            max_amount=MonetaryAmount(currency="INR", minor_units=150_000),
        ),
        workflow=workflow,
        issuer=human,
        signing_key=signing_key,
        task="Transfer exactly 150000 INR minor units to priya.",
        from_agent=planner,
        allowed_effect_types=("payment.transfer",),
        max_uses=1,
        manifest_hash=sealed.seal.manifest_hash,
        grant_id=f"executor-{workflow_id}",
        handoff_id=f"handoff-executor-{workflow_id}",
    )
    accept_handoff(engine, researcher_env, researcher)
    accepted = accept_handoff(engine, executor_env, executor)
    result = engine.commit(sealed, accepted.grant, fake_adapter, context=None)
    proof = engine.verify(sealed.manifest, result, fake_adapter, context=None)
    record_effect(
        workflow,
        engine=engine,
        sealed=sealed,
        grant=accepted.grant,
        commit_result=result,
        outcome_proof=proof,
    )
    export = export_workflow(workflow, keyring=engine.context.keyring, clock=engine.context.clock)
    return export


def test_three_agent_workflow_verifies_offline(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    export = _run_workflow(
        engine,
        fake_adapter,
        manifest_factory,
        human_principal,
        issuer_signing_key,
        fixed_clock.now(),
        workflow_id="wf-happy",
        manifest_id="manifest-happy",
    )
    assert len(export.handoffs) == 2
    assert len(export.passports) == 1
    assert export.passports[0].outcome_status == OutcomeStatus.VERIFIED_MATCH
    pack_result = verify_evidence_pack(export.evidence_packs[0])
    assert pack_result.all_verified
    workflow_result = verify_workflow_export(export)
    assert workflow_result.all_verified, workflow_result.reasons


def test_spliced_handoff_from_other_workflow_fails(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    now = fixed_clock.now()
    first = _run_workflow(
        engine_factory(),
        fake_adapter,
        manifest_factory,
        human_principal,
        issuer_signing_key,
        now,
        workflow_id="wf-a",
        manifest_id="manifest-a",
    )
    # A second engine: the first manifest is already VERIFIED, and authorize
    # cannot run again on that lifecycle.
    second = _run_workflow(
        engine_factory(),
        fake_adapter,
        manifest_factory,
        human_principal,
        issuer_signing_key,
        now,
        workflow_id="wf-b",
        manifest_id="manifest-b",
    )
    spliced_handoffs = (second.handoffs[0],) + first.handoffs[1:]
    spliced = first.model_copy(update={"handoffs": spliced_handoffs})
    spliced = spliced.model_copy(update={"export_hash": spliced.compute_export_hash()})
    result = verify_workflow_export(spliced)
    assert not result.all_verified
    assert any("workflow" in reason or "root grant" in reason for reason in result.reasons)

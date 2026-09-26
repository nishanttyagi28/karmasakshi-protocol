"""Handoffs that must fail closed."""

from __future__ import annotations

from datetime import timedelta

import pytest

from karmasakshi.delegation.revocation import MAX_DELEGATION_DEPTH
from karmasakshi.domain.common import MonetaryAmount, Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.errors import (
    ConstraintWideningError,
    GrantIssuerNotAuthorizedError,
    HandoffRejectedError,
)
from karmasakshi.grants.model import ScopeConstraints
from karmasakshi.handoff import accept_handoff, create_handoff, open_workflow


def _agent(principal_id: str) -> Principal:
    return Principal(principal_id=principal_id, principal_type=PrincipalType.AGENT)


def _authorize(engine, sealed, human, subject, signing_key, now, scope, effect_types, grant_id):
    return engine.authorize(
        sealed,
        issuer=human,
        subject=subject,
        audience=("payment.simulator",),
        allowed_effect_types=effect_types,
        scope=scope,
        not_before=now,
        expires_at=now + timedelta(hours=1),
        signing_key=signing_key,
        max_uses=2,
        grant_id=grant_id,
    )


def _sealed(engine, fake_adapter, manifest_factory, signing_key, manifest_id="handoff-manifest"):
    prepared = engine.prepare(
        fake_adapter,
        manifest_factory(manifest_id=manifest_id, effect_type="payment.transfer"),
        context=None,
    )
    return engine.seal(prepared, signing_key)


def test_wider_amount_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key)
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(max_amount=MonetaryAmount(currency="INR", minor_units=100_000)),
        ("payment.transfer",),
        "root-amount",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-amount")
    with pytest.raises(ConstraintWideningError):
        create_handoff(
            engine,
            root,
            _agent("executor"),
            ScopeConstraints(max_amount=MonetaryAmount(currency="INR", minor_units=700_000)),
            workflow=workflow,
            issuer=human_principal,
            signing_key=issuer_signing_key,
            task="pay more than the parent allows",
        )
    assert workflow.handoffs == []


def test_wider_recipient_scope_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-scope")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(recipients=("priya",)),
        ("payment.transfer",),
        "root-scope",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-scope")
    with pytest.raises(ConstraintWideningError):
        create_handoff(
            engine,
            root,
            _agent("executor"),
            ScopeConstraints(recipients=("priya", "ravi")),
            workflow=workflow,
            issuer=human_principal,
            signing_key=issuer_signing_key,
            task="add a recipient the parent does not allow",
        )


def test_agent_cannot_be_handoff_issuer(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-issuer")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-issuer",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-issuer")
    with pytest.raises(GrantIssuerNotAuthorizedError):
        create_handoff(
            engine,
            root,
            _agent("executor"),
            None,
            workflow=workflow,
            issuer=planner,
            signing_key=issuer_signing_key,
            task="planner tries to sign its own delegation",
        )


def test_wrong_receiver_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-addr")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-addr",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-addr")
    envelope = create_handoff(
        engine,
        root,
        _agent("executor"),
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="only the executor may accept this",
    )
    with pytest.raises(HandoffRejectedError, match="addressed to"):
        accept_handoff(engine, envelope, _agent("researcher"))


def test_tampered_envelope_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    executor = _agent("executor")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-tamper")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-tamper",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-tamper")
    envelope = create_handoff(
        engine,
        root,
        executor,
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="transfer the approved amount",
    )
    tampered = envelope.model_copy(update={"task": "transfer a different amount"})
    with pytest.raises(HandoffRejectedError, match="content hash"):
        accept_handoff(engine, tampered, executor)


def test_expired_handoff_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    executor = _agent("executor")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-expired")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-expired",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-expired")
    envelope = create_handoff(
        engine,
        root,
        executor,
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="do this before the window closes",
        expires_at=now + timedelta(seconds=30),
    )
    fixed_clock.advance(timedelta(minutes=5))
    with pytest.raises(HandoffRejectedError, match="expired"):
        accept_handoff(engine, envelope, executor)


def test_revoked_grandparent_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    researcher = _agent("researcher")
    executor = _agent("executor")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-revoke")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-revoke",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-revoke")
    mid = create_handoff(
        engine,
        root,
        researcher,
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="intermediate hop",
        grant_id="mid-revoke",
    )
    leaf = create_handoff(
        engine,
        mid.grant,
        executor,
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="leaf hop",
        from_agent=researcher,
        grant_id="leaf-revoke",
    )
    engine.context.grant_store.revoke(root.grant_id)
    with pytest.raises(HandoffRejectedError, match="revoked"):
        accept_handoff(engine, leaf, executor)


def test_missing_lineage_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    researcher = _agent("researcher")
    executor = _agent("executor")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-lineage")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-lineage",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-lineage")
    mid = create_handoff(
        engine,
        root,
        researcher,
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="middle of the chain",
        grant_id="mid-lineage",
    )
    leaf = create_handoff(
        engine,
        mid.grant,
        executor,
        None,
        workflow=workflow,
        issuer=human_principal,
        signing_key=issuer_signing_key,
        task="needs the middle row",
        from_agent=researcher,
        grant_id="leaf-lineage",
    )
    # The store has no public "forget lineage" API. Drop the intermediate
    # row so the ancestor walk cannot prove the chain.
    del engine.context.grant_store._lineage[mid.grant.grant_id]
    with pytest.raises(HandoffRejectedError, match="lineage"):
        accept_handoff(engine, leaf, executor)


def test_delegation_deeper_than_max_depth_is_rejected(
    engine_factory, fake_adapter, manifest_factory, human_principal, issuer_signing_key, fixed_clock
):
    engine = engine_factory()
    now = fixed_clock.now()
    planner = _agent("planner")
    sealed = _sealed(engine, fake_adapter, manifest_factory, issuer_signing_key, "manifest-depth")
    root = _authorize(
        engine,
        sealed,
        human_principal,
        planner,
        issuer_signing_key,
        now,
        ScopeConstraints(),
        ("payment.transfer",),
        "root-depth",
    )
    workflow = open_workflow(engine, root, workflow_id="wf-depth")
    parent = root
    sender = planner
    for depth in range(1, MAX_DELEGATION_DEPTH + 1):
        receiver = _agent(f"hop-{depth}")
        envelope = create_handoff(
            engine,
            parent,
            receiver,
            None,
            workflow=workflow,
            issuer=human_principal,
            signing_key=issuer_signing_key,
            task=f"hop {depth}",
            from_agent=sender,
            grant_id=f"hop-grant-{depth}",
            handoff_id=f"hop-handoff-{depth}",
        )
        parent = envelope.grant
        sender = receiver
    with pytest.raises(HandoffRejectedError, match="max depth"):
        create_handoff(
            engine,
            parent,
            _agent("hop-too-far"),
            None,
            workflow=workflow,
            issuer=human_principal,
            signing_key=issuer_signing_key,
            task="one hop past the ceiling",
            from_agent=sender,
        )

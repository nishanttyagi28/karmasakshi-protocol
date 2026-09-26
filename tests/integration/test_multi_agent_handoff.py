"""The offline multi-agent example: planner, researcher, executor."""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from examples.multi_agent_handoff.run_workflow import run_demo

from karmasakshi.passports.v2 import OutcomeStatus
from karmasakshi.portable import verify_evidence_pack


def test_example_workflow_ends_in_verified_passport_and_evidence_pack():
    result = run_demo()
    assert result["paused_status"] == "sealed"
    assert result["status"] == "verified"
    assert result["passport_status"] == OutcomeStatus.VERIFIED_MATCH.value
    assert result["evidence_verified"] is True
    export = result["export"]
    assert [envelope.to_agent.principal_id for envelope in export.handoffs] == [
        "researcher",
        "executor",
    ]
    assert export.handoffs[0].grant.allowed_effect_types == ("payment.read",)
    assert export.handoffs[1].grant.allowed_effect_types == ("payment.transfer",)
    assert export.handoffs[1].grant.scope.max_amount.minor_units == 150_000
    pack = verify_evidence_pack(export.evidence_packs[0])
    assert pack.all_verified
    assert result["balance"] == 1_000_000 - 150_000

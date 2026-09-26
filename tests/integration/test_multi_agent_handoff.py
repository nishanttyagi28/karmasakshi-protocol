"""The offline multi-agent example: planner, researcher, executor."""

from __future__ import annotations

import shlex

import pytest

pytest.importorskip("langgraph")

from examples.multi_agent_handoff.run_workflow import main, run_demo

from karmasakshi.cli.app import app as cli_app
from karmasakshi.cli.workspace import Workspace
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


def test_example_writes_a_workspace_the_cli_can_export(tmp_path, capsys, monkeypatch):
    pytest.importorskip("typer")
    pytest.importorskip("fastapi")
    pytest.importorskip("jinja2")
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from karmasakshi.api.app import create_app
    from karmasakshi.api.auth import DEV_MODE_ENV

    workspace = tmp_path / "ws"
    main(["--workspace", str(workspace)])
    printed = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("karmasakshi ")
    ]
    assert printed == [f"karmasakshi --workspace {workspace} workflow export wf-refund-8842"]
    args = shlex.split(printed[0])
    assert args[0] == "karmasakshi"
    exported = CliRunner().invoke(cli_app, args[1:])
    assert exported.exit_code == 0, exported.output
    flat = " ".join(exported.output.split())
    assert "2 handoff(s), 1 passport(s), 1 evidence pack(s); VERIFIED" in flat

    stored = Workspace(workspace)
    export = stored.load_workflow_export("wf-refund-8842")
    assert len(export.handoffs) == 2
    for envelope in export.handoffs:
        acceptance = stored.load_handoff_acceptance(envelope.handoff_id)
        assert acceptance is not None
        assert acceptance.accepted_by == envelope.to_agent.principal_id
        assert acceptance.content_hash == envelope.content_hash
        assert acceptance.workflow_id == envelope.workflow_id

    monkeypatch.setenv(DEV_MODE_ENV, "1")
    app = create_app(data_dir=tmp_path / "api-data")
    app.state.console_workspace = workspace
    client = TestClient(app)
    detail = client.get("/console/workflows/wf-refund-8842")
    assert detail.status_code == 200
    assert detail.text.count('badge-ok">accepted') == 2
    assert "not accepted" not in detail.text
    assert 'badge-ok">verified' in detail.text

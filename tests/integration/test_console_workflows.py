"""Read-only console pages over a CLI workspace."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient
from tests.unit.test_handoff_workflow import _run_workflow

from karmasakshi.api.app import create_app
from karmasakshi.api.auth import DEV_MODE_ENV
from karmasakshi.cli.workspace import Workspace
from karmasakshi.handoff import HandoffAcceptance


def test_console_workflow_pages_show_handoffs_passports_and_tamper(
    monkeypatch,
    tmp_path,
    engine_factory,
    fake_adapter,
    manifest_factory,
    human_principal,
    issuer_signing_key,
    fixed_clock,
):
    monkeypatch.setenv(DEV_MODE_ENV, "1")
    export = _run_workflow(
        engine_factory(),
        fake_adapter,
        manifest_factory,
        human_principal,
        issuer_signing_key,
        fixed_clock.now(),
        workflow_id="wf-console",
        manifest_id="manifest-console",
    )
    workspace = Workspace(tmp_path / "ws")
    workspace.ensure_initialized()
    workspace.save_workflow_export(export)
    for envelope in export.handoffs:
        workspace.save_handoff(envelope)
    executor = export.handoffs[1]
    workspace.save_handoff_acceptance(
        HandoffAcceptance(
            handoff_id=executor.handoff_id,
            workflow_id=executor.workflow_id,
            accepted_by=executor.to_agent.principal_id,
            accepted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            content_hash=executor.content_hash,
        )
    )

    app = create_app(data_dir=tmp_path / "api-data")
    app.state.console_workspace = workspace.root
    client = TestClient(app)

    listing = client.get("/console/workflows")
    assert listing.status_code == 200
    assert "wf-console" in listing.text
    assert "verified" in listing.text

    detail = client.get("/console/workflows/wf-console")
    assert detail.status_code == 200
    page = detail.text
    assert "planner" in page
    assert "researcher" in page
    assert "executor" in page
    assert "not accepted" in page
    assert 'badge-ok">accepted' in page
    assert "hash ok" in page
    assert "verified_match" in page
    assert "cap 150000 INR" in page

    rewritten = export.handoffs[0].model_copy(update={"task": "tampered after export"})
    tampered = export.model_copy(update={"handoffs": (rewritten,) + export.handoffs[1:]})
    workspace.save_workflow_export(tampered)
    tampered_page = client.get("/console/workflows/wf-console")
    assert tampered_page.status_code == 200
    assert "hash mismatch" in tampered_page.text
    assert "badge-bad" in tampered_page.text
    assert 'method="post"' not in tampered_page.text

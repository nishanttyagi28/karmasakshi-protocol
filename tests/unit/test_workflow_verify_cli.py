"""``workflow verify`` fails closed on a spliced evidence pack."""

from __future__ import annotations

import json

from tests.unit.test_handoff_workflow import _run_workflow
from typer.testing import CliRunner

from karmasakshi.cli.app import app

runner = CliRunner()


def test_workflow_verify_rejects_spliced_evidence_pack(
    tmp_path,
    engine_factory,
    fake_adapter,
    manifest_factory,
    human_principal,
    issuer_signing_key,
    fixed_clock,
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
    spliced = first.model_copy(update={"evidence_packs": (second.evidence_packs[0],)})
    spliced = spliced.model_copy(update={"export_hash": spliced.compute_export_hash()})
    path = tmp_path / "spliced.json"
    path.write_text(spliced.model_dump_json(), encoding="utf-8")
    result = runner.invoke(
        app,
        ["--workspace", str(tmp_path / "ws"), "--json", "workflow", "verify", str(path)],
    )
    assert result.exit_code == 2
    body = json.loads(result.output)
    assert body["all_verified"] is False
    assert any("evidence pack" in reason for reason in body["reasons"])

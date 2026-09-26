"""CLI smoke test: handoff create, accept, inspect, and workflow export."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from karmasakshi.cli.app import app

runner = CliRunner()


def _run(args: list[str]):
    result = runner.invoke(app, args)
    if result.exit_code != 0:
        raise AssertionError(
            f"CLI failed ({result.exit_code}): {result.output}\n{result.exception}"
        )
    return result


def test_cli_handoff_create_accept_inspect_export(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    db_path = str(tmp_path / "ledger.db")
    _run(workspace + ["init"])
    _run(workspace + ["key", "generate", "issuer-1"])
    prepared = _run(
        workspace
        + [
            "--json",
            "prepare",
            "--adapter",
            "sqlite",
            "--sqlite-db-path",
            db_path,
            "--row-operation",
            "insert",
            "--row-id",
            "acct-1",
            "--new-balance",
            "1000",
            "--actor-id",
            "planner",
            "--actor-type",
            "agent",
            "--principal-id",
            "approver-1",
            "--principal-type",
            "human",
            "--idempotency-key",
            "idem-handoff-cli",
        ]
    )
    manifest_id = json.loads(prepared.output)["manifest_id"]
    _run(workspace + ["seal", manifest_id, "--key-id", "issuer-1"])
    issued = _run(
        workspace
        + [
            "--json",
            "grant",
            "issue",
            manifest_id,
            "--issuer-id",
            "approver-1",
            "--subject-id",
            "planner",
            "--key-id",
            "issuer-1",
            "--audience",
            "sqlite.row",
        ]
    )
    grant_id = json.loads(issued.output)["grant_id"]
    created = _run(
        workspace
        + [
            "--json",
            "handoff",
            "create",
            grant_id,
            "--to-agent",
            "executor",
            "--task",
            "Insert the approved row and nothing else.",
            "--issuer-id",
            "approver-1",
            "--key-id",
            "issuer-1",
            "--workflow-id",
            "wf-cli",
        ]
    )
    created_body = json.loads(created.output)
    handoff_id = created_body["handoff_id"]
    inspected = _run(workspace + ["--json", "handoff", "inspect", handoff_id])
    assert json.loads(inspected.output)["content_hash_ok"] is True
    _run(workspace + ["handoff", "accept", handoff_id, "--agent-id", "executor"])
    exported = _run(workspace + ["--json", "workflow", "export", "wf-cli"])
    body = json.loads(exported.output)
    assert body["all_verified"] is True
    assert body["handoffs"] == 1

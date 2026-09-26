"""CLI handoff usability: passports, verify, scope flags, and duplicate ids."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from karmasakshi.cli.app import app
from karmasakshi.handoff import WorkflowExport

runner = CliRunner()


def _run(args: list[str]):
    result = runner.invoke(app, args)
    if result.exit_code != 0:
        raise AssertionError(
            f"CLI failed ({result.exit_code}): {result.output}\n{result.exception}"
        )
    return result


def _init(workspace: list[str]) -> None:
    _run(workspace + ["init"])
    _run(workspace + ["key", "generate", "issuer-1"])


def _payment_root(workspace: list[str]) -> tuple[str, str]:
    prepared = _run(
        workspace
        + [
            "--json",
            "prepare",
            "--adapter",
            "payment",
            "--source-account",
            "operating",
            "--beneficiary",
            "priya",
            "--amount-minor-units",
            "150000",
            "--currency",
            "INR",
            "--reference",
            "order-8842",
            "--fund-source-account",
            "1000000",
            "--actor-id",
            "planner",
            "--actor-type",
            "agent",
            "--principal-id",
            "approver-1",
            "--principal-type",
            "human",
            "--idempotency-key",
            "refund-priya-8842",
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
            "payment.simulator",
            "--max-uses",
            "2",
            "--ttl-seconds",
            "3600",
            "--max-amount-minor",
            "500000",
            "--currency",
            "INR",
            "--allowed-recipient",
            "priya",
            "--effect-type",
            "payment.read",
            "--effect-type",
            "payment.transfer",
        ]
    )
    return manifest_id, json.loads(issued.output)["grant_id"]


def _handoff(
    workspace: list[str],
    parent_grant_id: str,
    *,
    to_agent: str,
    task: str,
    handoff_id: str,
    effect_type: str,
    max_amount_minor: int,
    manifest_id: str | None = None,
) -> str:
    args = [
        "--json",
        "handoff",
        "create",
        parent_grant_id,
        "--to-agent",
        to_agent,
        "--from-agent",
        "planner",
        "--task",
        task,
        "--issuer-id",
        "approver-1",
        "--key-id",
        "issuer-1",
        "--workflow-id",
        "wf-refund-8842",
        "--handoff-id",
        handoff_id,
        "--effect-type",
        effect_type,
        "--recipient",
        "priya",
        "--max-amount-minor",
        str(max_amount_minor),
        "--max-uses",
        "1",
    ]
    if manifest_id is not None:
        args.extend(["--manifest-id", manifest_id])
    created = _run(workspace + args)
    return json.loads(created.output)["grant_id"]


def test_cli_workflow_export_includes_passport_and_evidence(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    _init(workspace)
    manifest_id, root_grant_id = _payment_root(workspace)
    _handoff(
        workspace,
        root_grant_id,
        to_agent="researcher",
        task="Read the refund beneficiary and amount. Do not transfer.",
        handoff_id="handoff-researcher",
        effect_type="payment.read",
        max_amount_minor=0,
    )
    executor_grant_id = _handoff(
        workspace,
        root_grant_id,
        to_agent="executor",
        task="Transfer exactly 150000 INR minor units to priya for order 8842.",
        handoff_id="handoff-executor",
        effect_type="payment.transfer",
        max_amount_minor=150000,
        manifest_id=manifest_id,
    )
    _run(workspace + ["handoff", "accept", "handoff-researcher", "--agent-id", "researcher"])
    _run(workspace + ["handoff", "accept", "handoff-executor", "--agent-id", "executor"])
    _run(
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            executor_grant_id,
            "--adapter",
            "payment",
            "--fund-source-account",
            "1000000",
            "--workflow-id",
            "wf-refund-8842",
            "--handoff-id",
            "handoff-executor",
        ]
    )
    exported = _run(workspace + ["workflow", "export", "wf-refund-8842"])
    assert "2 handoff(s), 1 passport(s), 1 evidence pack(s); VERIFIED" in " ".join(
        exported.output.split()
    )

    export_path = tmp_path / "wf.json"
    _run(workspace + ["workflow", "export", "wf-refund-8842", "-o", str(export_path)])
    verified = _run(workspace + ["--json", "workflow", "verify", str(export_path)])
    body = json.loads(verified.output)
    assert body["all_verified"] is True
    assert body["handoffs"] == 2
    assert body["passports"] == 1
    assert body["evidence_packs"] == 1

    export = WorkflowExport.model_validate_json(export_path.read_text(encoding="utf-8"))
    broken = export.model_copy(update={"export_hash": "sha256:" + "11" * 32})
    bad_export = tmp_path / "bad-export.json"
    bad_export.write_text(broken.model_dump_json(), encoding="utf-8")
    tampered_export = runner.invoke(app, workspace + ["workflow", "verify", str(bad_export)])
    assert tampered_export.exit_code == 2
    assert "export_hash" in tampered_export.output

    rewritten = export.handoffs[0].model_copy(
        update={"task": "rewritten after the hash was sealed"}
    )
    handoff_tampered = export.model_copy(update={"handoffs": (rewritten,) + export.handoffs[1:]})
    handoff_tampered = handoff_tampered.model_copy(
        update={"export_hash": handoff_tampered.compute_export_hash()}
    )
    bad_handoff = tmp_path / "bad-handoff.json"
    bad_handoff.write_text(handoff_tampered.model_dump_json(), encoding="utf-8")
    tampered_handoff = runner.invoke(app, workspace + ["workflow", "verify", str(bad_handoff)])
    assert tampered_handoff.exit_code == 2
    assert "content hash mismatch" in tampered_handoff.output

    listed = {"grant_ids": [export.root_grant.grant_id]}
    revocations = tmp_path / "revocations.json"
    revocations.write_text(json.dumps(listed), encoding="utf-8")
    revoked = runner.invoke(
        app,
        workspace + ["workflow", "verify", str(export_path), "--revocations", str(revocations)],
    )
    assert revoked.exit_code == 2
    assert "revoked grant" in revoked.output


def test_execute_rejects_unaccepted_wrong_workflow_and_mismatched_grant(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    _init(workspace)
    manifest_id, root_grant_id = _payment_root(workspace)
    executor_grant_id = _handoff(
        workspace,
        root_grant_id,
        to_agent="executor",
        task="Transfer exactly 150000 INR minor units to priya for order 8842.",
        handoff_id="handoff-executor",
        effect_type="payment.transfer",
        max_amount_minor=150000,
        manifest_id=manifest_id,
    )
    unaccepted = runner.invoke(
        app,
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            executor_grant_id,
            "--adapter",
            "payment",
            "--workflow-id",
            "wf-refund-8842",
            "--handoff-id",
            "handoff-executor",
        ],
    )
    assert unaccepted.exit_code == 2
    assert "has not been accepted" in unaccepted.output

    _run(workspace + ["handoff", "accept", "handoff-executor", "--agent-id", "executor"])
    wrong_workflow = runner.invoke(
        app,
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            executor_grant_id,
            "--adapter",
            "payment",
            "--workflow-id",
            "wf-other",
            "--handoff-id",
            "handoff-executor",
        ],
    )
    assert wrong_workflow.exit_code == 2
    assert "not wf-other" in wrong_workflow.output

    mismatched = runner.invoke(
        app,
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            root_grant_id,
            "--adapter",
            "payment",
            "--workflow-id",
            "wf-refund-8842",
            "--handoff-id",
            "handoff-executor",
        ],
    )
    assert mismatched.exit_code == 2
    assert "delegated grant" in mismatched.output

    exported = _run(workspace + ["--json", "workflow", "export", "wf-refund-8842"])
    body = json.loads(exported.output)
    assert body["passports"] == 0
    assert body["evidence_packs"] == 0


def test_grant_issue_scope_flags_and_widening_rejected(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    _init(workspace)
    db_path = str(tmp_path / "ledger.db")
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
            "idem-scope",
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
            "--max-amount-minor",
            "1000",
            "--currency",
            "INR",
            "--allowed-recipient",
            "priya",
            "--effect-type",
            "sqlite.row.insert",
        ]
    )
    grant_id = json.loads(issued.output)["grant_id"]
    grant_path = Path(tmp_path / "ws" / "grants" / f"{grant_id}.json")
    grant = json.loads(grant_path.read_text(encoding="utf-8"))
    assert grant["scope"]["max_amount"] == {"currency": "INR", "minor_units": 1000}
    assert grant["scope"]["recipients"] == ["priya"]
    assert grant["allowed_effect_types"] == ["sqlite.row.insert"]

    widened = runner.invoke(
        app,
        workspace
        + [
            "handoff",
            "create",
            grant_id,
            "--to-agent",
            "executor",
            "--task",
            "Try to raise the cap.",
            "--issuer-id",
            "approver-1",
            "--key-id",
            "issuer-1",
            "--workflow-id",
            "wf-widen",
            "--max-amount-minor",
            "5000",
        ],
    )
    assert widened.exit_code == 2
    assert "ConstraintWideningError" in widened.output


def test_duplicate_handoff_id_is_rejected(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    _init(workspace)
    db_path = str(tmp_path / "ledger.db")
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
            "acct-dup",
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
            "idem-dup",
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
    args = [
        "handoff",
        "create",
        grant_id,
        "--to-agent",
        "executor",
        "--task",
        "Do the one approved insert.",
        "--issuer-id",
        "approver-1",
        "--key-id",
        "issuer-1",
        "--workflow-id",
        "wf-dup",
        "--handoff-id",
        "handoff-fixed",
    ]
    _run(workspace + args)
    duplicate = runner.invoke(app, workspace + args)
    assert duplicate.exit_code == 2
    assert "already exists" in duplicate.output
    handoffs = list((tmp_path / "ws" / "handoffs").glob("*.json"))
    assert [path.name for path in handoffs] == ["handoff-fixed.json"]

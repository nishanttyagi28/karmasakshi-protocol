"""Payment simulator state survives a new CLI process, and funding hits the sealed account."""

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


def _prepare_issue(workspace: list[str], *, source_account: str) -> tuple[str, str]:
    _run(workspace + ["init"])
    _run(workspace + ["key", "generate", "issuer-1"])
    prepared = _run(
        workspace
        + [
            "--json",
            "prepare",
            "--adapter",
            "payment",
            "--source-account",
            source_account,
            "--beneficiary",
            "priya",
            "--amount-minor-units",
            "1500",
            "--currency",
            "INR",
            "--reference",
            "ref-pay-1",
            "--fund-source-account",
            "100000",
            "--actor-id",
            "executor",
            "--actor-type",
            "agent",
            "--principal-id",
            "approver-1",
            "--principal-type",
            "human",
            "--idempotency-key",
            "idem-pay-persist",
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
            "executor",
            "--key-id",
            "issuer-1",
            "--audience",
            "payment.simulator",
        ]
    )
    return manifest_id, json.loads(issued.output)["grant_id"]


def test_verify_sees_payment_from_a_previous_process(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    manifest_id, grant_id = _prepare_issue(workspace, source_account="operating")
    _run(
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            grant_id,
            "--adapter",
            "payment",
            "--fund-source-account",
            "100000",
        ]
    )
    verified = _run(workspace + ["--json", "verify", manifest_id, "--adapter", "payment"])
    body = json.loads(verified.output)
    assert body["matched_expected"] is True


def test_fund_account_id_must_match_manifest_source_and_does_not_fail_the_manifest(tmp_path):
    workspace = ["--workspace", str(tmp_path / "ws")]
    manifest_id, grant_id = _prepare_issue(workspace, source_account="operating")
    mismatched = runner.invoke(
        app,
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            grant_id,
            "--adapter",
            "payment",
            "--fund-source-account",
            "100000",
            "--fund-account-id",
            "acct-src",
        ],
    )
    assert mismatched.exit_code != 0
    assert "refusing to fund a different account" in mismatched.output
    assert "operating" in mismatched.output

    # The mismatch is rejected before commit, so the manifest is not stuck failed.
    _run(
        workspace
        + [
            "execute",
            manifest_id,
            "--grant-id",
            grant_id,
            "--adapter",
            "payment",
        ]
    )
    verified = _run(workspace + ["--json", "verify", manifest_id, "--adapter", "payment"])
    assert json.loads(verified.output)["matched_expected"] is True

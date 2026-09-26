"""Export a workflow record as one offline-verifiable file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.errors import WorkflowVerificationError
from karmasakshi.handoff import WorkflowExport, verify_workflow_export

workflow_app = typer.Typer(help="Export and check a multi-agent workflow.")


@workflow_app.command("export")
def export_cmd(
    ctx: typer.Context,
    workflow_id: Annotated[str, typer.Argument()],
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
) -> None:
    """Write the workflow file and verify it offline.

    The file embeds every handoff, Action Passport, and Evidence Pack
    recorded for this workflow. Verification uses only that file.
    """
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        export = workspace.load_workflow_export(workflow_id)
        result = verify_workflow_export(export)
        text = export.model_dump_json(indent=2)
        written = None
        if output is not None:
            output.write_text(text, encoding="utf-8")
            written = str(output)
        emit(
            {
                "workflow_id": workflow_id,
                "export_hash": export.export_hash,
                "handoffs": len(export.handoffs),
                "passports": len(export.passports),
                "evidence_packs": len(export.evidence_packs),
                "all_verified": result.all_verified,
                "reasons": list(result.reasons),
                "output": written,
            },
            as_json=as_json,
            human=(
                f"Workflow [bold]{workflow_id}[/bold]: "
                f"{len(export.handoffs)} handoff(s), {len(export.passports)} passport(s), "
                f"{len(export.evidence_packs)} evidence pack(s); "
                f"{'VERIFIED' if result.all_verified else 'FAILED'}"
                + (f" ({'; '.join(result.reasons)})" if result.reasons else "")
                + (f" -> {written}" if written else "")
            ),
        )
        if not result.all_verified:
            raise typer.Exit(code=2)

    run_guarded(as_json, _do)


@workflow_app.command("verify")
def verify_cmd(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="A workflow export JSON file")],
    revocations: Annotated[
        Path | None,
        typer.Option(
            "--revocations",
            help=(
                "Optional JSON file of grant ids. When set, verification also fails "
                "if any grant in a handoff chain is listed. Omit it to check the "
                "export as a historical record; revocation is already enforced at "
                "accept and commit time."
            ),
        ),
    ] = None,
) -> None:
    """Verify an exported workflow file offline. Exit 0 or 2."""
    as_json: bool = ctx.obj["json"]

    def _do() -> None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkflowVerificationError(f"could not read {path}: {exc}") from exc
        try:
            export = WorkflowExport.model_validate_json(raw)
        except ValidationError as exc:
            raise WorkflowVerificationError(f"not a workflow export: {exc}") from exc
        revoked = _load_revoked_grant_ids(revocations) if revocations is not None else None
        result = verify_workflow_export(export, revoked_grant_ids=revoked)
        emit(
            {
                "workflow_id": export.workflow_id,
                "export_hash": export.export_hash,
                "handoffs": len(export.handoffs),
                "passports": len(export.passports),
                "evidence_packs": len(export.evidence_packs),
                "all_verified": result.all_verified,
                "reasons": list(result.reasons),
            },
            as_json=as_json,
            human=(
                f"Workflow [bold]{export.workflow_id}[/bold]: "
                f"{len(export.handoffs)} handoff(s), {len(export.passports)} passport(s), "
                f"{len(export.evidence_packs)} evidence pack(s); "
                f"{'VERIFIED' if result.all_verified else 'FAILED'}"
                + (f" ({'; '.join(result.reasons)})" if result.reasons else "")
            ),
        )
        if not result.all_verified:
            raise typer.Exit(code=2)

    run_guarded(as_json, _do)


def _load_revoked_grant_ids(path: Path) -> set[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowVerificationError(f"could not read revocations file: {exc}") from exc
    if isinstance(raw, list):
        ids = raw
    elif isinstance(raw, dict) and isinstance(raw.get("grant_ids"), list):
        ids = raw["grant_ids"]
    else:
        raise WorkflowVerificationError(
            'revocations file must be a JSON list of grant ids or {"grant_ids": ["..."]}'
        )
    if not all(isinstance(item, str) and item for item in ids):
        raise WorkflowVerificationError("revocation grant ids must be non-empty strings")
    return set(ids)


__all__ = ["workflow_app"]

"""Export a workflow record as one offline-verifiable file."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from karmasakshi.cli.common import emit, run_guarded
from karmasakshi.cli.workspace import Workspace
from karmasakshi.handoff import verify_workflow_export

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


__all__ = ["workflow_app"]

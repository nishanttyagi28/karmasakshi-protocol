"""The ``karmasakshi`` CLI entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from karmasakshi import __version__
from karmasakshi.cli.agenteval_cmd import agenteval_app
from karmasakshi.cli.approve_cmd import approvals_app, approve
from karmasakshi.cli.assess_cmd import assess
from karmasakshi.cli.audit_cmd import audit_app
from karmasakshi.cli.common import console, emit
from karmasakshi.cli.compensation_cmd import compensation_app
from karmasakshi.cli.demo_cmd import demo
from karmasakshi.cli.doctor_cmd import doctor
from karmasakshi.cli.envelope_cmd import envelope_app
from karmasakshi.cli.evidence_pack_cmd import evidence_pack_app
from karmasakshi.cli.execute_cmd import compensate, execute
from karmasakshi.cli.execute_cmd import verify as execute_verify
from karmasakshi.cli.grant_cmd import grant_app
from karmasakshi.cli.graph_cmd import graph_app
from karmasakshi.cli.handoff_cmd import handoff_app
from karmasakshi.cli.key_cmd import key_app
from karmasakshi.cli.manifest_cmd import prepare, seal
from karmasakshi.cli.passport_cmd import passport
from karmasakshi.cli.policy_cmd import policy_app
from karmasakshi.cli.witness_cmd import witness_app
from karmasakshi.cli.workflow_cmd import workflow_app
from karmasakshi.cli.workspace import Workspace, default_workspace_path

app = typer.Typer(
    name="karmasakshi",
    help="KarmaSakshi Protocol -- seal the intended effect, witness the actual outcome.",
    no_args_is_help=True,
)

app.add_typer(audit_app, name="audit")
app.add_typer(grant_app, name="grant")
app.add_typer(graph_app, name="graph")
app.add_typer(envelope_app, name="envelope")
app.add_typer(compensation_app, name="compensation")
app.add_typer(key_app, name="key")
app.add_typer(policy_app, name="policy")
app.add_typer(approvals_app, name="approvals")
app.add_typer(witness_app, name="witness")
app.add_typer(evidence_pack_app, name="evidence-pack")
app.add_typer(handoff_app, name="handoff")
app.add_typer(workflow_app, name="workflow")
app.add_typer(agenteval_app, name="agenteval")


@app.callback()
def main_callback(
    ctx: typer.Context,
    workspace: Annotated[
        Path | None, typer.Option("--workspace", help="Workspace directory (default: .karmasakshi)")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON output")
    ] = False,
) -> None:
    ws_path = workspace or default_workspace_path()
    ctx.obj = {"workspace": Workspace(ws_path), "json": json_output}


@app.command()
def init(ctx: typer.Context) -> None:
    """Initialize a new local workspace (keys/, manifests/, grants/)."""
    workspace: Workspace = ctx.obj["workspace"]
    as_json: bool = ctx.obj["json"]
    workspace.ensure_initialized()
    emit(
        {"workspace": str(workspace.root)},
        as_json=as_json,
        human=f"Initialized workspace at [bold]{workspace.root}[/bold]",
    )


@app.command()
def version() -> None:
    """Print the installed karmasakshi version."""
    console.print(__version__)


app.command("prepare")(prepare)
app.command("assess")(assess)
app.command("approve")(approve)
app.command("seal")(seal)
app.command("execute")(execute)
app.command("verify")(execute_verify)
app.command("compensate")(compensate)
app.command("passport")(passport)
app.command("demo")(demo)
app.command("doctor")(doctor)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

__all__ = ["app", "main"]

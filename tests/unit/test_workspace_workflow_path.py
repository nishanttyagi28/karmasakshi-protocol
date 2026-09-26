"""Workflow ids from the CLI must not escape the workflows directory."""

from __future__ import annotations

import pytest

from karmasakshi.cli.workspace import Workspace


def test_load_workflow_export_rejects_path_escape(tmp_path):
    workspace = Workspace(tmp_path / "ws")
    workspace.ensure_initialized()
    secret = tmp_path / "ws" / "secret.json"
    secret.write_text('{"not": "a workflow"}', encoding="utf-8")

    with pytest.raises(ValueError, match="outside"):
        workspace.load_workflow_export("../secret")
    with pytest.raises(ValueError, match="outside"):
        workspace.load_workflow_export("/etc/passwd")

    assert workspace.has_workflow("../secret") is False
    assert secret.read_text(encoding="utf-8") == '{"not": "a workflow"}'

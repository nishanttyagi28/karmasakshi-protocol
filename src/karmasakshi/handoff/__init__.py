"""Multi-agent handoff: pass a narrower grant, then prove what ran.

See docs/multi-agent-handoff.md.
"""

from __future__ import annotations

from karmasakshi.handoff.model import (
    WORKFLOW_EXPORT_FORMAT,
    WORKFLOW_EXPORT_SCHEMA_VERSION,
    HandoffEnvelope,
    WorkflowExport,
)
from karmasakshi.handoff.service import (
    WorkflowRecord,
    WorkflowVerificationResult,
    accept_handoff,
    create_handoff,
    export_workflow,
    open_workflow,
    record_effect,
    verify_workflow_export,
    workflow_from_export,
)

__all__ = [
    "WORKFLOW_EXPORT_FORMAT",
    "WORKFLOW_EXPORT_SCHEMA_VERSION",
    "HandoffEnvelope",
    "WorkflowExport",
    "WorkflowRecord",
    "WorkflowVerificationResult",
    "accept_handoff",
    "create_handoff",
    "export_workflow",
    "open_workflow",
    "record_effect",
    "verify_workflow_export",
    "workflow_from_export",
]

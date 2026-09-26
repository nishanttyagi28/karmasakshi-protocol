"""Server-rendered local web console.

No JavaScript build pipeline: plain HTML forms posting back to these
routes. Requires authentication in any non-development configuration, same
as the JSON API (see karmasakshi.api.auth) -- the dev-mode banner in
base.html makes unauthenticated local mode visually unmistakable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from karmasakshi.api.auth import is_dev_mode, require_auth
from karmasakshi.api.state import ApiState
from karmasakshi.cli.workspace import Workspace, default_workspace_path
from karmasakshi.domain.common import Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.errors import KarmaSakshiError
from karmasakshi.grants.model import ExecutionGrant, ScopeConstraints
from karmasakshi.handoff import verify_workflow_export
from karmasakshi.handoff.model import HandoffEnvelope, WorkflowExport

console_router = APIRouter(prefix="/console", dependencies=[Depends(require_auth)])

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _state(request: Request) -> ApiState:
    return request.app.state.karmasakshi  # type: ignore[no-any-return]


def _audit_status(state: ApiState) -> bool:
    try:
        state.engine.context.audit.verify_chain()
        return True
    except KarmaSakshiError:
        return False


@console_router.get("/")
def dashboard(request: Request) -> HTMLResponse:
    state = _state(request)
    all_manifests = []
    pending = []
    for mid, sealed in state.sealed_manifests.items():
        lifecycle_state = state.engine.get_lifecycle_state(mid).value
        row = {
            "manifest_id": mid,
            "effect_type": sealed.manifest.effect_type,
            "target_resource": sealed.manifest.target_resource,
            "risk": sealed.manifest.risk.value,
            "reversibility": sealed.manifest.reversibility.value,
            "lifecycle_state": lifecycle_state,
        }
        all_manifests.append(row)
        if lifecycle_state == "sealed":
            pending.append(row)
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "dev_mode": is_dev_mode(),
            "kill_switch_engaged": state.kill_switch_engaged,
            "audit_ok": _audit_status(state),
            "event_count": len(state.engine.context.audit.all_events()),
            "pending": pending,
            "all_manifests": all_manifests,
        },
    )


@console_router.get("/manifests/{manifest_id}")
def manifest_detail(manifest_id: str, request: Request) -> HTMLResponse:
    state = _state(request)
    sealed = state.sealed_manifests[manifest_id]
    return _templates.TemplateResponse(
        request,
        "manifest_detail.html",
        {
            "dev_mode": is_dev_mode(),
            "manifest": sealed.manifest,
            "seal": sealed.seal,
            "lifecycle_state": state.engine.get_lifecycle_state(manifest_id).value,
            "grant_ids": state.grants_by_manifest.get(manifest_id, []),
        },
    )


@console_router.post("/manifests/{manifest_id}/approve")
def approve(
    manifest_id: str,
    request: Request,
    issuer_id: Annotated[str, Form()],
    issuer_type: Annotated[str, Form()],
    subject_id: Annotated[str, Form()],
    max_uses: Annotated[int, Form()] = 1,
    ttl_seconds: Annotated[int, Form()] = 300,
) -> RedirectResponse:
    state = _state(request)
    sealed = state.sealed_manifests[manifest_id]
    now = datetime.now(timezone.utc)
    grant = state.engine.authorize(
        sealed,
        issuer=Principal(principal_id=issuer_id, principal_type=PrincipalType(issuer_type)),
        subject=Principal(principal_id=subject_id, principal_type=PrincipalType.AGENT),
        audience=(sealed.manifest.adapter.adapter_id,),
        allowed_effect_types=(sealed.manifest.effect_type,),
        scope=ScopeConstraints(),
        not_before=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        signing_key=state.signing_key,
        max_uses=max_uses,
    )
    state.register_grant(manifest_id, grant)
    return RedirectResponse(f"/console/manifests/{manifest_id}", status_code=303)


@console_router.post("/manifests/{manifest_id}/deny")
def deny(
    manifest_id: str, request: Request, reason: Annotated[str, Form()] = ""
) -> RedirectResponse:
    state = _state(request)
    sealed = state.sealed_manifests[manifest_id]
    state.engine.context.audit.record(
        event_type="manifest.authorization_denied",
        decision="denied",
        manifest_id=manifest_id,
        manifest_hash=sealed.seal.manifest_hash,
        metadata={"reason": reason[:200]},
    )
    return RedirectResponse("/console/", status_code=303)


@console_router.get("/grants")
def grants_view(request: Request) -> HTMLResponse:
    state = _state(request)
    rows = []
    for g in state.grants.values():
        rows.append(
            {
                "grant_id": g.grant_id,
                "subject": g.subject.principal_id,
                "issuer": g.issuer.principal_id,
                "use_count": state.engine.context.grant_store.get_use_count(g.grant_id),
                "max_uses": g.max_uses,
                "revoked": state.engine.context.grant_store.is_revoked(g.grant_id),
                "parent_grant_id": g.parent_grant_id,
            }
        )
    return _templates.TemplateResponse(
        request, "grants.html", {"dev_mode": is_dev_mode(), "grants": rows}
    )


@console_router.post("/grants/{grant_id}/revoke")
def revoke(grant_id: str, request: Request) -> RedirectResponse:
    state = _state(request)
    grant = state.grants[grant_id]
    manifest_id = next(
        (mid for mid, ids in state.grants_by_manifest.items() if grant_id in ids), grant_id
    )
    state.engine.revoke(grant, manifest_id, revoked_by=grant.issuer)
    return RedirectResponse(request.headers.get("referer", "/console/grants"), status_code=303)


def _console_workspace(request: Request) -> Workspace | None:
    configured = getattr(request.app.state, "console_workspace", None)
    if configured:
        return Workspace(Path(configured))
    if os.environ.get("KARMASAKSHI_CONSOLE_WORKSPACE") or os.environ.get("KARMASAKSHI_HOME"):
        return Workspace(default_workspace_path())
    candidate = Path.cwd() / ".karmasakshi"
    if candidate.is_dir():
        return Workspace(candidate)
    return None


def _scope_label(grant: ExecutionGrant) -> str:
    parts: list[str] = []
    if grant.scope.max_amount is not None:
        amount = grant.scope.max_amount
        parts.append(f"cap {amount.minor_units} {amount.currency}")
    else:
        parts.append("no amount cap")
    if grant.scope.recipients:
        parts.append("recipients " + ", ".join(grant.scope.recipients))
    parts.append("effects " + ", ".join(grant.allowed_effect_types))
    return "; ".join(parts)


def _acceptance_label(workspace: Workspace, envelope: HandoffEnvelope) -> str:
    hash_ok = envelope.compute_content_hash() == envelope.content_hash
    if not hash_ok:
        return "tampered"
    acceptance = workspace.load_handoff_acceptance(envelope.handoff_id)
    if (
        acceptance is not None
        and acceptance.accepted_by == envelope.to_agent.principal_id
        and acceptance.content_hash == envelope.content_hash
        and acceptance.workflow_id == envelope.workflow_id
    ):
        return "accepted"
    return "not accepted"


def _workflow_summary(workspace: Workspace, workflow_id: str) -> dict[str, Any]:
    try:
        export = workspace.load_workflow_export(workflow_id)
    except (OSError, ValidationError, ValueError):
        return {
            "workflow_id": workflow_id,
            "handoffs": "-",
            "passports": "-",
            "evidence_packs": "-",
            "status": "unreadable",
        }
    result = verify_workflow_export(export)
    return {
        "workflow_id": export.workflow_id,
        "handoffs": len(export.handoffs),
        "passports": len(export.passports),
        "evidence_packs": len(export.evidence_packs),
        "status": "verified" if result.all_verified else "failed",
    }


def _workflow_detail(workspace: Workspace, export: WorkflowExport) -> dict[str, Any]:
    result = verify_workflow_export(export)
    handoffs = []
    for envelope in export.handoffs:
        handoffs.append(
            {
                "handoff_id": envelope.handoff_id,
                "from_agent": envelope.from_agent.principal_id,
                "to_agent": envelope.to_agent.principal_id,
                "depth": envelope.delegation_depth,
                "scope": _scope_label(envelope.grant),
                "accepted": _acceptance_label(workspace, envelope),
                "hash_ok": envelope.compute_content_hash() == envelope.content_hash,
            }
        )
    passports = [
        {
            "manifest_id": passport.manifest_id,
            "grant_id": passport.grant_id or "-",
            "outcome_status": passport.outcome_status.value,
        }
        for passport in export.passports
    ]
    evidence_packs = []
    for pack, pack_result in zip(export.evidence_packs, result.evidence_pack_results, strict=False):
        evidence_packs.append(
            {
                "manifest_id": pack.manifest_id,
                "verified": pack_result.all_verified,
                "reasons": "; ".join(pack_result.reasons),
            }
        )
    return {
        "verified": result.all_verified,
        "reasons": list(result.reasons),
        "handoffs": handoffs,
        "passports": passports,
        "evidence_packs": evidence_packs,
    }


@console_router.get("/workflows")
def workflows_view(request: Request) -> HTMLResponse:
    workspace = _console_workspace(request)
    rows: list[dict[str, Any]] = []
    root = None
    if workspace is not None and workspace.workflows_dir.is_dir():
        root = str(workspace.root)
        for workflow_id in workspace.list_workflow_ids():
            rows.append(_workflow_summary(workspace, workflow_id))
    return _templates.TemplateResponse(
        request,
        "workflows.html",
        {"dev_mode": is_dev_mode(), "workflows": rows, "workspace_root": root},
    )


@console_router.get("/workflows/{workflow_id}")
def workflow_detail(workflow_id: str, request: Request) -> HTMLResponse:
    if workflow_id != Path(workflow_id).name or workflow_id in {".", ".."}:
        return HTMLResponse("Workflow not found", status_code=404)
    workspace = _console_workspace(request)
    context: dict[str, Any] = {
        "dev_mode": is_dev_mode(),
        "workflow_id": workflow_id,
        "error": None,
        "verified": False,
        "reasons": [],
        "handoffs": [],
        "passports": [],
        "evidence_packs": [],
    }
    if workspace is None or not (workspace.workflows_dir / f"{workflow_id}.json").is_file():
        return HTMLResponse("Workflow not found", status_code=404)
    try:
        export = workspace.load_workflow_export(workflow_id)
    except (OSError, ValidationError, ValueError) as exc:
        context["error"] = str(exc)
        return _templates.TemplateResponse(request, "workflow_detail.html", context)
    context.update(_workflow_detail(workspace, export))
    context["workflow_id"] = export.workflow_id
    return _templates.TemplateResponse(request, "workflow_detail.html", context)


@console_router.get("/audit")
def audit_view(request: Request) -> HTMLResponse:
    state = _state(request)
    events = state.engine.context.audit.all_events()
    return _templates.TemplateResponse(
        request,
        "audit.html",
        {"dev_mode": is_dev_mode(), "audit_ok": _audit_status(state), "events": events},
    )


@console_router.post("/kill-switch/engage")
def engage(request: Request) -> RedirectResponse:
    _state(request).kill_switch_engaged = True
    return RedirectResponse("/console/", status_code=303)


@console_router.post("/kill-switch/disengage")
def disengage(request: Request) -> RedirectResponse:
    _state(request).kill_switch_engaged = False
    return RedirectResponse("/console/", status_code=303)


__all__ = ["console_router"]

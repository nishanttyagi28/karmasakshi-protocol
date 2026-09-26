"""Handoff envelopes and the workflow record that ties them to passports.

A handoff is not a new kind of grant. It is a signed child grant (issued by
``engine.delegate``) plus the facts a receiving agent must check before it
acts: who sent it, who it is for, and a canonical hash over that content.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from karmasakshi.canonical.serialize import canonical_hash
from karmasakshi.config.clock import ensure_utc
from karmasakshi.domain.common import Principal
from karmasakshi.domain.enums import PrincipalType
from karmasakshi.grants.model import ExecutionGrant
from karmasakshi.passports.v2 import ActionPassportV2
from karmasakshi.portable.model import EmbeddedVerificationKey, EvidencePack

WORKFLOW_EXPORT_FORMAT: Literal["workflow_export.v1"] = "workflow_export.v1"
WORKFLOW_EXPORT_SCHEMA_VERSION = "1.0"


def _validate_sha256(value: str) -> str:
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise ValueError("must be a sha256:<hex> digest")
    return value


class HandoffEnvelope(BaseModel):
    """One agent handing a narrower grant to another agent.

    ``content_hash`` covers every field except itself. ``lineage`` is the
    grant chain from the workflow root through ``grant``, so a receiver can
    run ``verify_delegation_chain`` without a side channel.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str
    workflow_id: str
    from_agent: Principal
    to_agent: Principal
    task: str
    grant: ExecutionGrant
    parent_grant_id: str
    delegation_depth: int
    lineage: tuple[ExecutionGrant, ...]
    created_at: datetime
    expires_at: datetime
    content_hash: str

    @field_validator("handoff_id", "workflow_id", "parent_grant_id")
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        if not value or len(value) > 128:
            raise ValueError("identifier fields must be 1-128 chars")
        return value

    @field_validator("task")
    @classmethod
    def _validate_task(cls, value: str) -> str:
        if not value.strip() or len(value) > 1024:
            raise ValueError("task must be 1-1024 characters")
        return value

    @field_validator("from_agent", "to_agent")
    @classmethod
    def _validate_agent(cls, value: Principal) -> Principal:
        if value.principal_type != PrincipalType.AGENT:
            raise ValueError("handoff parties must be agent principals")
        return value

    @field_validator("created_at", "expires_at")
    @classmethod
    def _validate_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("content_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        return _validate_sha256(value)

    @field_validator("delegation_depth")
    @classmethod
    def _validate_depth(cls, value: int) -> int:
        if value < 1:
            raise ValueError("delegation_depth must be >= 1")
        return value

    @model_validator(mode="after")
    def _validate_lineage_shape(self) -> HandoffEnvelope:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be strictly after created_at")
        if len(self.lineage) != self.delegation_depth + 1:
            raise ValueError("delegation_depth does not match lineage length")
        if self.lineage[-1].grant_id != self.grant.grant_id:
            raise ValueError("lineage must end with the delegated grant")
        if self.grant.parent_grant_id != self.parent_grant_id:
            raise ValueError("parent_grant_id does not match the delegated grant")
        if self.lineage[-2].grant_id != self.parent_grant_id:
            raise ValueError("lineage parent is not the grant named by parent_grant_id")
        if self.grant.subject.principal_id != self.to_agent.principal_id:
            raise ValueError("delegated grant subject must be the receiving agent")
        if self.lineage[-2].subject.principal_id != self.from_agent.principal_id:
            raise ValueError("parent grant subject must be the sending agent")
        return self

    def canonical_content(self) -> dict[str, object]:
        """The bytes that ``content_hash`` covers. The hash field is excluded."""
        data: dict[str, object] = self.model_dump(mode="json", exclude={"content_hash"})
        return data

    def compute_content_hash(self) -> str:
        return canonical_hash(self.canonical_content())


class HandoffAcceptance(BaseModel):
    """Record that one agent accepted one envelope at one content hash.

    Written by ``handoff accept``. ``execute`` refuses to treat a handoff as
    ready unless this record is present and still matches the envelope.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str
    workflow_id: str
    accepted_by: str
    accepted_at: datetime
    content_hash: str

    @field_validator("handoff_id", "workflow_id", "accepted_by")
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        if not value or len(value) > 128:
            raise ValueError("identifier fields must be 1-128 chars")
        return value

    @field_validator("accepted_at")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("content_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        return _validate_sha256(value)


class WorkflowExport(BaseModel):
    """One file a reviewer can check without the live engine.

    Embedded evidence packs are the packs ``build_evidence_pack`` already
    produces. This model does not replace them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    export_format: Literal["workflow_export.v1"] = WORKFLOW_EXPORT_FORMAT
    schema_version: str = WORKFLOW_EXPORT_SCHEMA_VERSION
    generated_at: datetime
    export_hash: str
    workflow_id: str
    created_at: datetime
    root_grant: ExecutionGrant
    handoffs: tuple[HandoffEnvelope, ...] = ()
    passports: tuple[ActionPassportV2, ...] = ()
    evidence_packs: tuple[EvidencePack, ...] = ()
    verification_keys: tuple[EmbeddedVerificationKey, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _validate_schema(cls, value: str) -> str:
        if value != WORKFLOW_EXPORT_SCHEMA_VERSION:
            raise ValueError(
                f"WorkflowExport requires schema_version {WORKFLOW_EXPORT_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("workflow_id")
    @classmethod
    def _validate_workflow_id(cls, value: str) -> str:
        if not value or len(value) > 128:
            raise ValueError("workflow_id must be 1-128 chars")
        return value

    @field_validator("generated_at", "created_at")
    @classmethod
    def _validate_times(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("export_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        return _validate_sha256(value)

    def deterministic_payload(self) -> dict[str, object]:
        data: dict[str, object] = self.model_dump(mode="json")
        data.pop("generated_at", None)
        data.pop("export_hash", None)
        return data

    def compute_export_hash(self) -> str:
        return canonical_hash(self.deterministic_payload())


__all__ = [
    "WORKFLOW_EXPORT_FORMAT",
    "WORKFLOW_EXPORT_SCHEMA_VERSION",
    "HandoffAcceptance",
    "HandoffEnvelope",
    "WorkflowExport",
]

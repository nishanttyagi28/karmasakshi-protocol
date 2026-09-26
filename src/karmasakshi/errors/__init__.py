"""Shared exception hierarchy for KarmaSakshi Protocol.

All security-relevant failures raise a subclass of :class:`KarmaSakshiError` so
callers can fail closed deterministically instead of relying on generic
exceptions. Messages must never leak secrets (private keys, raw tokens,
full credential material) -- see docs/security-model.md.
"""

from __future__ import annotations


class KarmaSakshiError(Exception):
    """Base class for all protocol errors."""


# --- Manifest / canonicalization -------------------------------------------------


class ManifestError(KarmaSakshiError):
    """Base class for manifest validation and integrity errors."""


class ManifestValidationError(ManifestError):
    """Manifest failed schema validation (unknown/ambiguous/oversized fields)."""


class ManifestTamperedError(ManifestError):
    """Recomputed manifest hash does not match the sealed hash."""


class SchemaVersionError(ManifestError):
    """Manifest or grant declares an unsupported schema version."""


# --- Cryptography ------------------------------------------------------------------


class CryptoError(KarmaSakshiError):
    """Base class for signing/verification errors."""


class UnknownKeyError(CryptoError):
    """Signature references a key id not present in the trusted keyring."""


class InvalidSignatureError(CryptoError):
    """Signature verification failed."""


class UnsupportedAlgorithmError(CryptoError):
    """Requested signing/verification algorithm is not supported."""


class KeyLoadError(CryptoError):
    """Key material could not be loaded safely."""


# --- Grants --------------------------------------------------------------------


class GrantError(KarmaSakshiError):
    """Base class for execution grant errors."""


class GrantExpiredError(GrantError):
    pass


class GrantNotYetValidError(GrantError):
    pass


class GrantRevokedError(GrantError):
    pass


class GrantExhaustedError(GrantError):
    """Grant has reached its maximum number of uses."""


class GrantManifestMismatchError(GrantError):
    """Grant's bound manifest_hash does not match the manifest being executed."""


class GrantAudienceError(GrantError):
    """Adapter identity/version is not within the grant's audience."""


class GrantIssuerNotAuthorizedError(GrantError):
    """Attempted to issue/authorize a grant from a principal that may not authorize
    (e.g. the model/agent itself), violating invariant #30."""


# --- Delegation ------------------------------------------------------------------


class DelegationError(KarmaSakshiError):
    """Base class for delegation/attenuation errors."""


class DelegationLineageError(DelegationError):
    """Delegation lineage is incomplete, cyclic, or exceeds the depth bound
    so deep revocation safety cannot be proven (fail closed)."""


class ConstraintWideningError(DelegationError):
    """Child grant attempts to widen authority relative to its parent."""


class IncomparableConstraintError(DelegationError):
    """Two constraints cannot be safely compared; treated as widening (fail closed)."""


class HandoffError(KarmaSakshiError):
    """Base class for multi-agent handoff failures."""


class HandoffRejectedError(HandoffError):
    """A handoff was rejected. The receiver must not act."""


class WorkflowVerificationError(HandoffError):
    """A workflow export failed offline verification."""


# --- State machine ---------------------------------------------------------------


class StateMachineError(KarmaSakshiError):
    """Base class for lifecycle state machine errors."""


class IllegalTransitionError(StateMachineError):
    """Attempted transition is not permitted from the current state."""


# --- TOCTOU / preconditions -------------------------------------------------------


class PreconditionError(KarmaSakshiError):
    """Base class for precondition/TOCTOU failures."""


class StaleManifestError(PreconditionError):
    """Observed external state no longer matches the sealed preconditions."""


# --- Storage / atomicity ----------------------------------------------------------


class StoreError(KarmaSakshiError):
    """Base class for storage backend errors. Storage failures must fail closed."""


class StoreUnavailableError(StoreError):
    """Storage backend is unreachable or in an indeterminate state."""


class ConcurrentConsumptionError(StoreError):
    """A concurrent process already consumed/reserved this grant."""


# --- Audit ---------------------------------------------------------------------


class AuditError(KarmaSakshiError):
    """Base class for audit journal errors."""


class AuditWriteError(AuditError):
    """Audit record could not be durably written; execution must not proceed."""


class AuditTamperedError(AuditError):
    """Audit chain hash verification failed."""


# --- Causal effect graphs --------------------------------------------------------


class CausalGraphError(KarmaSakshiError):
    """Causal graph is invalid, untrusted, or cannot answer the requested query."""


# --- Decision envelopes / atomic plan authorization (extreme-v2 Phase 6) ---------


class DecisionEnvelopeError(KarmaSakshiError):
    """Base class for constrained decision-envelope errors."""


class DecisionEnvelopeIssuerNotAuthorizedError(DecisionEnvelopeError):
    """Attempted to build a decision envelope with an agent principal as
    issuer. Envelope constraints bound authorization, so the same rule that
    forbids an agent from issuing an ExecutionGrant (invariant #30) applies."""


class DecisionEnvelopeConstraintError(DecisionEnvelopeError):
    """A concrete value or manifest falls outside an envelope constraint, or
    a child envelope widens a parent constraint."""


class DecisionEnvelopeSubstitutionError(DecisionEnvelopeError):
    """Deterministic parameter substitution could not resolve a complete,
    constraint-satisfying parameter dict from the supplied choices."""


class DecisionEnvelopeTamperedError(DecisionEnvelopeError):
    """Recomputed decision-envelope hash does not match the expected hash."""


class DecisionEnvelopeMismatchError(DecisionEnvelopeError):
    """A grant bound to one decision-envelope hash was presented with a
    different (or missing) envelope at commit time."""


class DecisionEnvelopeNotYetValidError(DecisionEnvelopeError):
    """Decision envelope's ``not_before`` is in the future relative to now."""


class DecisionEnvelopeExpiredError(DecisionEnvelopeError):
    """Decision envelope's ``expires_at`` has passed relative to now."""


class AtomicPlanError(KarmaSakshiError):
    """Atomic plan (causal-graph-bound) authorization failed: missing
    membership, hash mismatch, or unverified graph."""


# --- Compensation manifests / passports (extreme-v2 Phase 7) -----------------


class CompensationError(KarmaSakshiError):
    """Base class for compensation-manifest / Compensation Passport errors."""


class CompensationBindingError(CompensationError):
    """A compensation effect is not correctly bound to its original sealed
    manifest hash, or a caller attempted to compensate the wrong original."""


class CompensationNotAuthorizedError(CompensationError):
    """Attempted to commit an authorized compensation path without a valid
    grant bound to the compensation manifest (and original hash)."""


class CompensationPassportIntegrityError(CompensationError):
    """A Compensation Passport failed integrity checks, or a caller attempted
    to treat a mutated Action Passport as a compensation record."""


# --- Independent witness quorum (extreme-v2 Phase 9) -------------------------


class WitnessError(KarmaSakshiError):
    """Base class for independent witness quorum errors."""


class WitnessIssuerNotAuthorizedError(WitnessError):
    """An agent principal attempted to sign a witness statement."""


class WitnessExpiredError(WitnessError):
    """Witness statement expiry has passed relative to now."""


class WitnessBatchTooLargeError(WitnessError):
    """More witness statements were submitted than the policy bound allows."""


class WitnessQuorumNotMetError(WitnessError):
    """Independent witness quorum was not satisfied."""


# --- Evidence quality / provenance (extreme-v2 Phase 10) ---------------------


class EvidenceError(KarmaSakshiError):
    """Base class for evidence quality and provenance errors."""


class EvidenceQualityError(EvidenceError):
    """Evidence set failed freshness, provenance, or quality policy checks."""


class EvidenceBatchTooLargeError(EvidenceError):
    """More evidence records were submitted than the policy bound allows."""


# --- Authority budgets (extreme-v2 Phase 12) ---------------------------------


class AuthorityBudgetError(KarmaSakshiError):
    """Base class for authority budget errors."""


class AuthorityBudgetExhaustedError(AuthorityBudgetError):
    """Requested consumption exceeds remaining budget capacity."""


# --- Saga orchestration (extreme-v2 Phase 8) ---------------------------------


class SagaError(KarmaSakshiError):
    """Base class for durable saga orchestration errors."""


class SagaPlanError(SagaError):
    """Saga plan construction or identity check failed."""


class SagaOrderingError(SagaError):
    """Causal graph could not yield a deterministic saga step order."""


class SagaIllegalTransitionError(SagaError):
    """Requested saga run/step transition is illegal for the current status."""


class SagaAmbiguousStepError(SagaError):
    """A saga step outcome is ambiguous; blind re-commit is refused."""


class SagaGraphMismatchError(SagaError):
    """Presented causal graph does not match the saga plan binding."""


# --- Policy bundles ----------------------------------------------------------------


class PolicyBundleError(KarmaSakshiError):
    """Base class for signed policy bundle errors."""


class PolicyBundleTamperedError(PolicyBundleError):
    """Recomputed policy bundle hash does not match the sealed hash."""


class PolicyBundleNotYetEffectiveError(PolicyBundleError):
    """Policy bundle's ``effective_from`` is in the future relative to now."""


class PolicyBundleExpiredError(PolicyBundleError):
    """Policy bundle's ``effective_until`` has passed relative to now."""


class PolicyBundleTypeMismatchError(PolicyBundleError):
    """Policy bundle's ``policy_type`` does not match what the caller expected."""


class PolicyBundleMismatchError(PolicyBundleError):
    """A grant bound to one policy bundle hash was presented with a different
    (or missing) policy bundle at commit time -- a policy swap after
    authorization must never silently alter what was approved."""


class PolicyBundleIssuerNotAuthorizedError(PolicyBundleError):
    """Attempted to build a policy bundle with an agent principal as its
    issuer. A policy bundle's thresholds influence authorization outcomes,
    so the same rule that forbids an agent from issuing an
    ExecutionGrant (invariant #30) applies here: an agent may propose or
    draft policy content, but may never be recorded as the authorizing
    issuer of the bundle that governs it."""


# --- Multi-party approval / quorum ------------------------------------------------


class ApprovalError(KarmaSakshiError):
    """Base class for multi-party approval and quorum errors."""


class ApprovalIssuerNotAuthorizedError(ApprovalError):
    """Attempted to sign an approval statement with an agent principal as
    the approver. An agent may never satisfy human/service approval
    quorum -- invariant #30 applied to approvals."""


class ApprovalExpiredError(ApprovalError):
    """Approval statement's ``expires_at`` has passed relative to now."""


class ApprovalBatchTooLargeError(ApprovalError):
    """More approval statements were submitted than the policy's
    ``max_statements_considered`` bound allows -- rejected outright
    (fail closed) rather than silently truncated, which could drop a
    statement that would have changed the quorum outcome."""


class QuorumNotMetError(ApprovalError):
    """The submitted approval statements did not satisfy the bound
    ApprovalPolicy's quorum requirement; see the accompanying
    ``QuorumResult`` for exactly why."""


# --- Separation of duties (extreme-v2 Phase 4) --------------------------------------


class SeparationOfDutyError(KarmaSakshiError):
    """Base class for separation-of-duty errors."""


class SeparationOfDutyViolationError(SeparationOfDutyError):
    """A ``RoleAssignment`` was found to violate a bound
    ``SeparationOfDutyPolicy``'s forbidden role-pair matrix -- the same
    principal was recorded as holding two roles that policy forbids from
    ever coinciding for one manifest (e.g. sealer and approver). See the
    accompanying ``SeparationOfDutyResult`` for exactly which role pairs
    and principals were involved."""


class RoleAssignmentError(SeparationOfDutyError):
    """A ``RoleAssignment`` failed structural validation, or a caller
    presented a role assignment bound to a different manifest hash than
    the one being authorized."""


# --- Adapters --------------------------------------------------------------------


class AdapterError(KarmaSakshiError):
    """Base class for effect adapter errors."""


class AdapterMismatchError(AdapterError):
    """Manifest's adapter identity/version does not match the executing adapter."""


class UntrustedAdapterError(AdapterError):
    """Adapter identity/version is missing from, revoked on, or outside the
    declared capability of the trusted adapter registry (fail closed)."""


class AdapterConformanceError(AdapterError):
    """An adapter failed a Phase 18 conformance-kit check (fail closed)."""


class UnsupportedCompensationError(AdapterError):
    """The effect is irreversible and cannot honestly be compensated."""


class BlastRadiusExceededError(AdapterError):
    """Effect would affect more resources than the configured safety ceiling."""


class RecipientNotAllowedError(AdapterError):
    """A recipient/target is not on the adapter's configured allow-list."""


# --- Multi-tenant control plane (extreme-v2 Phase 19) -----------------------------


class TenantError(KarmaSakshiError):
    """Base class for tenant isolation errors."""


class UnknownTenantError(TenantError):
    """Referenced tenant_id is not present in the tenant registry."""


class TenantIsolationError(TenantError):
    """Cross-tenant access, suspended tenant, or tenant uncertainty (fail closed)."""


class InvalidOrganizationIdError(TenantError, ValueError):
    """Organization/tenant id fails canonical validation (RA-001): it must
    never be usable as anything other than a single, contained filesystem
    path segment under the configured tenant data root.

    Also a ``ValueError`` so it is caught by ``dataclass``/Pydantic
    validators exactly like the ``ValueError``s they already raise for other
    fields (e.g. ``Tenant.display_name``)."""


# --- Resource / DoS protection (extreme-v2 Phase 20) ------------------------------


class ResourceProtectionError(KarmaSakshiError):
    """Base class for resource and DoS protection errors."""


class RequestTooLargeError(ResourceProtectionError):
    """Request body / Content-Length exceeds the configured ceiling."""


class RateLimitExceededError(ResourceProtectionError):
    """Client exceeded the configured request rate limit."""


# --- Portable evidence packs (extreme-v2 Phase 24) --------------------------------


class PortableEvidenceError(KarmaSakshiError):
    """Base class for portable Evidence Pack errors."""


class EvidencePackAssemblyError(PortableEvidenceError):
    """The artifacts supplied to build an Evidence Pack are inconsistent
    (e.g. passport and sealed manifest reference different manifest ids)."""


class EvidencePackTooLargeError(PortableEvidenceError):
    """The audit slice or keyring exceeds the pack's embedding ceiling."""


# --- AgentEval failure-memory loop (extreme-v2 Phase 25) --------------------------


class FailureMemoryError(KarmaSakshiError):
    """Base class for AgentEval failure-memory store errors."""


class FailureMemoryCorruptedError(FailureMemoryError):
    """A stored failure-memory record is not valid JSON or does not match
    the ``RegressionFixture`` schema."""


# --- Commercial Gateway: durable organization model (Milestone A) ----------------


class GatewayError(KarmaSakshiError):
    """Base class for Gateway commercial-layer errors."""


class GatewayMigrationError(GatewayError):
    """A Gateway database migration failed to apply."""


class OrganizationNotFoundError(GatewayError):
    """Referenced org_id does not exist."""


class OrganizationAlreadyExistsError(GatewayError):
    """org_id is already registered."""


class OrganizationSuspendedError(GatewayError):
    """The organization is suspended and cannot be used."""


class GatewayUserNotFoundError(GatewayError):
    """No user with that org_id + email exists."""


class GatewayUserAlreadyExistsError(GatewayError):
    """A user with that org_id + email is already registered."""


class GatewayAgentNotFoundError(GatewayError):
    """No refund agent with that org_id + agent_id is registered."""


class GatewayAgentAlreadyExistsError(GatewayError):
    """A different refund-agent registration already uses this org/id."""


class GatewayAdapterNotFoundError(GatewayError):
    """No adapter with that org_id + adapter_id is registered."""


class GatewayAdapterAlreadyExistsError(GatewayError):
    """A different adapter registration already uses this org/id."""


class GatewayAuthenticationError(GatewayError):
    """Email/password did not authenticate for the given organization."""


class CrossOrganizationAccessError(GatewayError):
    """An operation attempted to read or act across an organization
    boundary (invariant: fail closed on tenant uncertainty, same posture
    as Phase 19's ``TenantIsolationError``)."""

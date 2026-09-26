# Changelog

All notable changes to this project are documented in this file. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Multi-agent handoff. `create_handoff` delegates through `engine.delegate`
  (attenuation, budget inheritance, depth, and the agent-issuer ban stay
  where they already are). `accept_handoff` checks the envelope hash,
  `verify_grant`, `verify_delegation_chain`, and
  `assert_no_revoked_ancestors` before the receiver acts.
- A workflow export that links those handoffs to Action Passports and
  Evidence Packs. Offline verification calls `verify_evidence_pack` and
  rejects a handoff spliced from another workflow.
- CLI: `karmasakshi handoff create|accept|inspect` and
  `karmasakshi workflow export`.
- Offline example: `examples/multi_agent_handoff/run_workflow.py`
  (planner, researcher, executor; no model calls).
- Security invariants 75–80. See
  [docs/multi-agent-handoff.md](docs/multi-agent-handoff.md).

## [0.2.0] - 2026-07-30

Evaluation-ready self-hosted Milestone A release of the protocol after the
full extreme-v2 roadmap and an independent release-audit remediation cycle.
This is still an experimental package (protocol schema major `1`). It is
**not** production-ready software, not certified, not a formal proof, and
not a real payment-provider integration.

Package metadata and `karmasakshi.__version__` are `0.2.0`. Detailed phase
ledger: [docs/extreme-v2-build-status.md](docs/extreme-v2-build-status.md).
Final release review:
[docs/product/FINAL_RELEASE_REVIEW.md](docs/product/FINAL_RELEASE_REVIEW.md).

### Added

- **25-phase protocol roadmap complete** on top of the v0.1.0 core: Effect
  Intelligence, signed policy bundles, multi-party authorization, separation
  of duties, causal effect graphs, decision envelopes, compensation
  manifests, saga orchestration, witness quorum, authority budgets, Action
  Passport V2, portable evidence, observability, multi-tenant control plane,
  trusted adapter registry, resource protection, crash recovery / durable
  lifecycle stores, production signer interfaces (local/emulated only),
  transactional outbox, and AgentEval failure-memory tooling. Phase-by-phase
  evidence lives in the extreme-v2 build status ledger.

- **Effect Intelligence and signed policy binding.** Deterministic
  `karmasakshi.intelligence` assessment over a sealed
  `IntelligencePolicy` bundle. Grants bind `policy_bundle_hash` at
  authorization; commit re-verifies the same hash. Gateway refunds freeze
  the policy that produced the *proposal-time* assessment so a later policy
  activation cannot silently rebind an already assessed effect (including
  across process restart).

- **Multi-party authorization and separation of duties.** M-of-N
  `ApprovalStatement` quorums (`karmasakshi.approval`) and optional
  forbidden-role-pair enforcement (`karmasakshi.duty`) before a grant is
  issued. Agent principals cannot satisfy human approval quorums.

- **Causal graphs, compensation, and saga capabilities.** Signed causal
  DAGs (`karmasakshi.causal`); compensation as a *separately* authorized
  effect with its own passport (`karmasakshi.compensation`); bounded saga
  orchestration (`karmasakshi.saga`). Compensation is best-effort and not a
  guaranteed rollback of irreversible external effects.

- **Witness evidence and Action Passport V2.** Witness quorum statements
  (`karmasakshi.witness`) and additive Action Passport schema 2.0
  (`karmasakshi.passports.v2`) with content hash and independent
  seal/grant/audit re-verification. V2 is not a separately signed
  credential.

- **Durable lifecycle and restart recovery.** Per-tenant SQLite lifecycle,
  grant, audit, and outbox stores; Gateway write-through refund-journey
  persistence (`karmasakshi.gateway.refund_state`); startup rehydration of
  organizations and refund state so a process restart against the same data
  directory does not 500 org-scoped routes or drop committed refunds,
  Passports, or audit search.

- **Tenant isolation and fail-closed signing-key restoration.** Canonical
  organization IDs (RA-001 path containment); per-tenant data directories;
  durable Ed25519 signing keys with public-identity sidecars; missing,
  corrupt, or mismatched key material fails closed when durable artifacts
  already exist (no silent replacement identity).

- **Gateway refund vertical slice.** Organization bootstrap, session auth,
  agent/adapter inventory, policy activation, propose → assess → approve →
  commit → verify → Passport → evidence pack, ambiguous-outcome recovery,
  and compensation as a separate HTTP-authorized effect — each org on an
  isolated `MultiTenantControlPlane` runtime. See
  [docs/gateway.md](docs/gateway.md).

- **Typed sync and async Python SDK.** `karmasakshi.sdk.GatewayClient` and
  `AsyncGatewayClient` covering the Gateway surface with server pydantic
  models reused for responses. See [docs/sdk.md](docs/sdk.md).

- **Control Center and buyer-facing acceptance.** Server-rendered
  authenticated UI at `/control-center/` (async SDK + Gateway). Packaged
  `karmasakshi-acceptance` drives 25 real checks through API, SDK, and UI;
  Docker Compose evaluation profile and CI `compose-acceptance` job. See
  [docs/product/BUYER_EVALUATION.md](docs/product/BUYER_EVALUATION.md) and
  [docs/control-center.md](docs/control-center.md).

### Fixed (release-audit remediation)

Independent Milestone A release audit on `cea2496` returned **NO-GO**
([docs/product/RELEASE_AUDIT.md](docs/product/RELEASE_AUDIT.md) — preserved
unchanged). Remediation (PRs #48–#49) closed Critical/High/Medium findings
including tenant path escape, restart rehydration, active-policy assessment,
ambiguous recovery consistency, owner-only policy/user gates, packaging and
dependency audit gaps, and residual durability issues:

- committed/verified refund detail, list, Passport, and audit after restart
- proposal-time policy hash binding at approve and execute
- durable per-tenant signing keys so policy propose and Passport
  `grant_verified` survive restart
- fail-closed behaviour for missing/corrupt/mismatched signing keys

Evidence:
[docs/product/RELEASE_AUDIT_REMEDIATION.md](docs/product/RELEASE_AUDIT_REMEDIATION.md),
[docs/product/POST_REMEDIATION_AUDIT.md](docs/product/POST_REMEDIATION_AUDIT.md),
[docs/product/FINAL_RELEASE_REVIEW.md](docs/product/FINAL_RELEASE_REVIEW.md).

### Verification (on the evaluation-ready main line)

Recorded on the final release review of merge commit `99c6ec7` (and matching
local re-runs):

- **1049 passed, 8 skipped** — skips are Redis integration tests when no
  Redis is reachable at `localhost:6379`; CI Redis jobs still exercise them
- **90.49%–90.50%** line+branch coverage (`--cov-fail-under=90`)
- ruff, strict mypy, Bandit, pip-audit, package build, and Twine clean
- buyer acceptance **25/25 PASS**; Docker Compose acceptance green in Linux CI
- fresh adversarial checks for restart recovery, proposal-time policy
  binding, and signing-key durability/fail-closed

### Known limitations

Unchanged in kind; see [docs/limitations.md](docs/limitations.md):

- Payment-simulator account balances are process-local and reset on restart;
  Gateway/protocol evidence of what happened does not.
- Local evaluation auth is password + session tokens, not production IAM,
  SSO, or complete enterprise RBAC.
- Compensation workflows remain limited (single authorized call; not a full
  multi-party quorum journey).
- No third-party certification, formal verification, or real bank / mail /
  payment-provider connectors.
- Low-severity deferred audit findings (RA-012–014) remain documented and
  out of the remediation pass.
- SQLite single-node only for the evaluation product; Redis tests need a
  real Redis instance.

## [0.1.0] - 2026-07-27

Initial feature-complete reference implementation of protocol schema
version `1.0`.

### Added

- Core protocol: `EffectManifest`, canonical serialization/hashing,
  `Seal`/`SealedManifest`, `ExecutionGrant`, Ed25519 signing/verification
  with a rotatable keyring.
- Lifecycle state machine (`PROPOSED` → `PREPARED` → `SEALED` →
  `AUTHORIZED` → `COMMITTING` → `COMMITTED` → `VERIFIED`, plus `FAILED`,
  `REVOKED`, `EXPIRED`, `COMPENSATING`, `COMPENSATED`) and the
  `KarmaSakshiEngine` orchestrator enforcing all 30 documented security
  invariants (see `docs/security-model.md`).
- Storage backends: in-memory, SQLite (durable, single-node), and Redis
  (distributed, Lua-script atomic consumption).
- Delegation with attenuation (parent→child narrowing, multi-hop chain
  verification, one-hop revocation propagation at commit time).
- Append-only, hash-chained audit journal (in-memory and SQLite backends)
  and Action Passport generation (JSON/Markdown/HTML).
- Three reference Effect Adapters: SQLite row insert/update/delete
  (parameterized SQL, optimistic concurrency), an email sandbox (never
  sends real email), and a deterministic payment simulator (never moves
  real money; includes injectable failure and ambiguous-timeout modes).
- Optional LangGraph integration: pause-for-authorization, resume, commit,
  verify, with the signing key kept out of graph state/checkpoints.
- Optional AgentEval bridge: versioned, neutral regression-fixture export.
- CLI (`karmasakshi`): init, key management, prepare/seal, grant
  issue/verify/delegate/revoke/inspect, execute/verify/compensate, audit
  list/show/verify, passport generation, a 15-scenario deterministic demo
  suite, and a doctor command.
- Optional FastAPI control plane and a server-rendered local console,
  with fail-closed bearer-token authentication and an emergency kill
  switch.
- 272+ tests (unit, integration, property-based via Hypothesis, and
  adversarial) plus documentation covering architecture, protocol spec,
  the security model, threat model, storage semantics, crash recovery,
  adapter authoring, and honest limitations.

### Known limitations

See [docs/limitations.md](docs/limitations.md). Highlights: no
third-party security audit; Redis backend tests only run against a real
reachable Redis instance; the AgentEval bridge is a neutral export format,
not a verified-compatible schema implementation; SQLite storage is
single-node only.

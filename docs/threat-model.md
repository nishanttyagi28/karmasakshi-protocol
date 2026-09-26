# Threat Model

**This document describes what KarmaSakshi Protocol is designed to
resist and, equally importantly, what it is not. It is not a security
certification.**

## In scope: what the protocol defends against

1. **A compromised or misaligned agent proposing a different effect than
   what a human approved.** Defended by binding authorization to a
   canonical hash of the exact resolved effect (`EffectManifest`), not to
   a tool name or free-text description. Any field change invalidates the
   seal (invariant #3).
2. **A resolved effect whose target/amount changes between preparation and
   execution (TOCTOU).** Defended by `StateFingerprint` preconditions
   re-checked immediately before commit (invariant #14).
3. **An agent issuing its own authorization.** Structurally impossible —
   `issue_grant()` rejects an agent-typed issuer outright (invariant #30).
4. **A grant being replayed, reused beyond its intended count, or used
   concurrently by racing callers.** Defended by atomic
   reserve/commit/release semantics in the grant store (invariants #4, #5).
5. **A sub-agent or delegated grant exceeding the authority it was
   delegated.** Defended by attenuation checks on every dimension
   (invariants #15-#18).
6. **Trusting a tool's "success" response as proof the real-world effect
   matched intent.** Defended by a separate, independent verification step
   that re-observes external state (invariants #20, #21) — demonstrated
   concretely by the payment simulator's ambiguous-timeout mode.
7. **Silent tampering with the audit trail after the fact.** Defended by a
   hash-chained, append-only journal (invariant #22).
8. **A forged signature on an otherwise-unchanged manifest or grant.**
   Defended by full cryptographic verification (hash *and* signature) at
   every consumption point, including `commit()`, not just `authorize()`.
9. **Storage or audit-sink outages producing an indeterminate "maybe it
   worked" state.** Defended by fail-closed error propagation — no
   `except: pass` anywhere in the commit path (invariants #10, #23).
10. **One agent handing another a wider, expired, revoked, or rewritten
    grant, or splicing a handoff from a different workflow into an
    export.** The receiver must accept the envelope before it acts
    (invariants #75–#77). The issuer of every hop is still a human or
    service (invariant #78). Attenuation is the existing delegate check
    (invariant #79). `verify_workflow_export` rejects a foreign handoff
    and re-checks each embedded Evidence Pack (invariant #80).

## New trusted component: the Effect Intelligence Engine

`karmasakshi.intelligence` (see [docs/effect-intelligence.md](effect-intelligence.md))
adds a deterministic risk-scoring engine over `EffectManifest` +
`IntelligencePolicy` + `AssessmentFacts`. It contains no LLM and makes no
security decision by itself -- it is pure, versioned arithmetic, and its
output (`EffectAssessment`) is recorded in the same hash-chained audit
journal as every other engine step.

**It does not currently change the security model.** `authorize()` and
`commit()` do not read `EffectAssessment.recommendation`; a `BLOCK`
recommendation is informational only in this protocol version. Do not
treat calling `assess()` (or seeing `recommendation: block` in a passport)
as evidence that a manifest was actually blocked -- check the lifecycle
state and the presence/absence of a valid `ExecutionGrant` instead.
Signed policy bundles (below) and M-of-N authorization (below) are now
implemented, but neither one reads `EffectAssessment.recommendation`
automatically yet -- a caller must still explicitly configure
`IntelligencePolicy`/`ApprovalPolicy` thresholds; nothing derives one
from the other's output today.

## New trusted components: signed policy bundles, multi-party authorization, and separation of duties

`karmasakshi.policy` (see [docs/policy-bundles.md](policy-bundles.md)),
`karmasakshi.approval` (see
[docs/multi-party-authorization.md](multi-party-authorization.md)), and
`karmasakshi.duty` (see
[docs/separation-of-duties.md](separation-of-duties.md)) add real
structural security properties, unlike the advisory Effect Intelligence
Engine above:

- **A grant bound to a policy bundle cannot commit against a different,
  missing, tampered, or expired one** (invariant #31). This *does* change
  the security model: `IntelligencePolicy` is no longer a purely
  in-process, unsigned value when a caller chooses to bind one --
  `PolicyBundle`'s signature and effective window are enforced both at
  `authorize()` and `commit()` time.
- **A grant issued via `authorize_with_quorum()` is structurally
  impossible to obtain without a satisfied M-of-N approval quorum**
  (invariant #33), with hard-coded exclusions for agent approvers,
  self-approval by the proposer, and approval by the executing subject
  (invariants #34, #35).
- **A grant issued with a bound separation-of-duty policy is structurally
  impossible to obtain if any principal holds both roles of a forbidden
  pair** (invariant #37) -- e.g. the same principal sealing and approving
  one manifest. The check is a set intersection over the combined role
  assignment, not a tally: adding compliant approvers alongside a
  conflicted one does not dilute the violation (invariant #38 covers its
  determinism).

All three remain **additive**: the original single-issuer `authorize()`/
`commit()` path (with no policy bundle, no approval set, no separation
policy) is unchanged and still fully supported -- these are opt-in
stronger guarantees, not a replacement for the base protocol. New trust
assumptions this introduces:

- Whoever holds a signing key trusted for `policy_type="approval.v1"`
  bundles can set arbitrarily loose quorum rules (e.g.
  `required_approvals=1` with no role requirements) -- the cryptographic
  binding proves *which* policy was used, not that the policy itself was
  wise. Reviewing who is authorized to sign approval (and separation-of-duty)
  policy bundles is a deployment responsibility, same as for Effect
  Intelligence policies.
- The reference API's approval-statement endpoint signs every submitted
  statement with the control plane's own single service key rather than a
  distinct per-approver key -- see docs/multi-party-authorization.md's
  "Honesty note" for the exact implication and why the CLI does not share
  this limitation.
- Approval roles (`ApprovalStatement.role`) are self-asserted by the
  signer, not checked against any external identity/role directory --
  there is no RBAC system in this protocol version (the commercial
  roadmap's enterprise "approval groups" is the planned identity-backed
  successor).
- Separation-of-duty role facts beyond proposer/executor/approver (e.g.
  `sealer`, `witness`) are entirely caller-supplied
  (`role_assignment`), with the same trust level already extended to
  `issuer`/`subject`/`proposer` -- there is no cryptographic proof that
  the principal named as `sealer` actually performed the sealing action
  (see docs/separation-of-duties.md's Known limitations).

## Explicitly out of scope

- **Compromise of the machine running the engine.** If an attacker has
  arbitrary code execution on the host, they can read private key material
  from wherever it's loaded (file, env var) — this protocol does not
  implement HSM/KMS integration, secure enclaves, or memory protection
  against a co-resident attacker. Production deployments should load keys
  from a proper secret manager and consider hardware-backed signing;
  neither is implemented here.
- **A malicious or compromised adapter.** The engine trusts that an
  adapter's `prepare()` accurately resolves the request and that
  `validate_preconditions()`/`verify()` accurately reflect external state.
  A deliberately dishonest adapter (e.g. one that always reports
  `matched_expected=True` regardless of reality) defeats verification.
  Phase 17's trusted adapter registry constrains *which* exact
  `(adapter_id, adapter_version)` pairs and effect types may run when
  configured, but does not make a dishonest registered adapter truthful.
  Adapters remain part of the trusted computing base — write and review
  them accordingly (see [docs/adapter-authoring.md](adapter-authoring.md)
  and [docs/trusted-adapter-registry.md](trusted-adapter-registry.md)).
- **Denial of service.** Nothing here rate-limits proposal volume,
  protects the audit journal from unbounded growth, or defends the API/CLI
  process against resource exhaustion.
- **Side-channel attacks on the signing operation** (timing attacks on
  Ed25519 verification, etc.) — the `cryptography` library's implementation
  is trusted as-is; no additional hardening is applied.
- **Multi-machine distributed consensus beyond what Redis's atomicity
  provides.** The Redis backend gives atomic single-key check-and-set
  across processes/machines sharing one Redis instance; it does not
  implement Raft/Paxos-style consensus, leader election, or partition
  tolerance beyond what Redis itself offers.
- **Formal verification.** The 38 invariants in
  [docs/security-model.md](security-model.md) are tested (unit,
  property-based, adversarial), not proven with a theorem prover or model
  checker.
- **Recovering from a compromised signing key.** Key rotation is
  supported (`Keyring.add_key`/`.remove_key`), but there is no automated
  revocation-and-reissue workflow for grants signed by a key discovered to
  be compromised after the fact.
- **Enforcement of Effect Intelligence Engine recommendations.** See "New
  trusted component" above -- `assess()` scores and records, it does not
  gate.
- **Role-based access control / identity directory for approval roles.**
  `ApprovalStatement.role` is self-asserted by the signer; nothing
  verifies a claimed role ("finance", "security") against an external
  system of record. See docs/multi-party-authorization.md.
- **Per-approver keys in the reference API.** The `/manifests/{id}/approvals`
  endpoint signs on behalf of the caller-supplied approver identity using
  the control plane's one service key. The CLI does not have this gap
  (each workspace key is a distinct keypair).

## Trust boundaries (see also docs/architecture.md)

```text
[Agent / LLM]  --produces-->  [raw request]
     |  no signing key, no grant-issuance capability, ever
     v
[Adapter.prepare()]  --produces-->  [EffectManifest]
     v
[Engine.seal()]  (holds the signing key)  --produces-->  [SealedManifest]
     v
[Human or Service principal]  --calls engine.authorize()-->  [ExecutionGrant]
     v
[Engine.commit()]  (the only place that decides whether to call the adapter)
     v
[Adapter.commit()]  --performs-->  [External system]
```

The signing key lives only inside the process holding `EngineContext`/the
CLI workspace/the API's `ApiState` — it is never serialized into a
manifest, grant, audit event, passport, or anything handed back to an
agent (see the LangGraph integration's
`test_interrupt_payload_never_contains_signing_material`).

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).

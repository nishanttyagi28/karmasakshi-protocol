# CLI Reference

`karmasakshi [--workspace PATH] [--json] <command> ...`

`--workspace` defaults to `.karmasakshi` under the current directory (or
`$KARMASAKSHI_HOME`). `--json` switches every command to machine-readable
JSON output. Exit codes: `0` success, `1` general/validation error, `2`
security-denied (any `KarmaSakshiError` subclass).

## Workspace

The workspace holds dev keys (`keys/`), sealed manifests and their commit/
verify/compensation results (`manifests/`), grants (`grants/`), a
SQLite-backed grant store (`grants.db`), and a SQLite-backed audit journal
(`audit.db`) — everything needed to run `prepare`, `seal`, `grant issue`,
`execute`, `verify`, `compensate`, `passport`, and `audit` as *separate*
process invocations while keeping a consistent lifecycle state. See
`karmasakshi.cli.workspace.Workspace.reconstruct_lifecycle_state()` and
[docs/state-machine.md](state-machine.md) for how that consistency is
maintained across processes.

## Commands

```text
karmasakshi init
karmasakshi key generate <key_id>
karmasakshi key list

karmasakshi prepare --adapter {sqlite,email,payment} --actor-id ID [adapter-specific options...]
karmasakshi assess <manifest_id> [--delegation-depth N] [--historical-recurrence-count N]
    [--historical-failure-count N] [--provider-idempotent unknown|yes|no]
    [--compensation-feasible unknown|yes|no] [--cross-tenant] [--unusual-parameter-change]
    [--policy-violation TEXT ...] [--from-audit-history]
karmasakshi seal <manifest_id> --key-id ID

karmasakshi policy create <bundle_id> --issuer-id ID [--issuer-type human|service] [--block-threshold N]
    [--review-threshold N] [--restricted-effect-type TEXT ...] [--sensitive-target-pattern REGEX ...]
karmasakshi policy create-approval <bundle_id> --issuer-id ID [--required-approvals N]
    [--required-role TEXT ...] [--cooling-off-seconds N]
karmasakshi policy create-separation <bundle_id> --issuer-id ID [--forbidden-pair "role_a:role_b" ...]
karmasakshi policy sign <bundle_id> --key-id ID
karmasakshi policy verify <bundle_id>

karmasakshi approve <manifest_id> --approver-id ID --key-id ID --approval-policy-bundle-id ID
    [--approver-type human|service] [--role TEXT] [--decision approve|dissent] [--reason TEXT]
karmasakshi approvals inspect <manifest_id> --approval-policy-bundle-id ID --proposer-id ID --subject-id ID

karmasakshi grant issue <manifest_id> --issuer-id ID --subject-id ID --key-id ID [--audience ...] [--max-uses N]
    [--max-amount-minor N] [--currency CODE] [--allowed-recipient ID ...] [--effect-type TYPE ...]
    [--policy-bundle-id ID] [--separation-policy-bundle-id ID] [--role "role_name:principal_id" ...]
    [--decision-envelope-id ID | --causal-graph-id ID]
karmasakshi grant issue-with-quorum <manifest_id> --approval-policy-bundle-id ID --grant-issuer-id ID
    --proposer-id ID --subject-id ID --key-id ID [--audience ...] [--policy-bundle-id ID]
    [--separation-policy-bundle-id ID] [--role "role_name:principal_id" ...]
karmasakshi grant verify <grant_id>
karmasakshi grant delegate <parent_grant_id> --issuer-id ID --subject-id ID --key-id ID [--max-uses N] [--ttl-seconds N]
karmasakshi grant revoke <grant_id> [--manifest-id ID]
karmasakshi grant inspect <grant_id>

karmasakshi envelope create <envelope_id> --effect-type TYPE --adapter-id ID --target-resource RES
    [--constraint name=kind:...] --key-id ID [--sign/--no-sign]
karmasakshi envelope verify <envelope_id>
karmasakshi envelope substitute <envelope_id> [--choice name=value ...]

karmasakshi execute <manifest_id> --grant-id ID --adapter {sqlite,email,payment}
    [--policy-bundle-id ID] [--decision-envelope-id ID] [--causal-graph-id ID]
    [adapter-specific options...]
karmasakshi verify <manifest_id> --adapter {sqlite,email,payment} [...]
karmasakshi compensate <manifest_id> --adapter {sqlite,email,payment} [...]
karmasakshi compensation prepare <original_manifest_id> [--key-id ID]
karmasakshi compensation authorize <original_id> <compensation_id> [--issuer-id ID] [--key-id ID]
karmasakshi compensation execute <original_id> <compensation_id> --grant-id ID --adapter {sqlite,email,payment} [...]
karmasakshi compensation passport <original_id> <compensation_id> [--grant-id ID]
karmasakshi witness sign <manifest_id> --witness-id ID --key-id ID --observed-digest D
karmasakshi witness evaluate <manifest_id> --observed-digest D [--assert] [--required-witnesses N]

karmasakshi audit list
karmasakshi audit show <manifest_id>
karmasakshi audit verify

karmasakshi passport <manifest_id> [--format json|markdown|html] [--version v1|v2] [--grant-id ID] [-o FILE]

karmasakshi evidence-pack build <manifest_id> [--grant-id ID] [-o FILE]
karmasakshi evidence-pack verify <pack_file>

karmasakshi handoff create <parent_grant_id> --to-agent ID --task TEXT --issuer-id ID --key-id ID --workflow-id ID
    [--handoff-id ID] [--from-agent ID] [--effect-type TYPE ...] [--recipient ID ...]
    [--max-amount-minor N] [--max-uses N] [--ttl-seconds N] [--manifest-id ID]
karmasakshi handoff accept <handoff_id> --agent-id ID
karmasakshi handoff inspect <handoff_id>
karmasakshi workflow export <workflow_id> [-o FILE]
karmasakshi workflow verify <file> [--revocations FILE]
karmasakshi execute <manifest_id> --grant-id ID --adapter NAME [--workflow-id ID --handoff-id ID]

karmasakshi agenteval record <manifest_id> --failure-category CAT [--invariant STR]
karmasakshi agenteval history

karmasakshi demo --all
karmasakshi doctor
karmasakshi version
```

## Adapter-specific `prepare`/`execute`/`verify`/`compensate` options

Only the three reference adapters are wired into the CLI (`sqlite`,
`email`, `payment`) — this is deliberate: the CLI resolves `--adapter` to a
concrete class, never dynamically imports/executes arbitrary adapter code
from a string, to avoid an arbitrary-code-loading surface in a
security-focused tool. Third-party adapters are used via the Python API.

- `--adapter sqlite`: `--sqlite-db-path PATH` (required), `--sqlite-table
  NAME`, `--row-operation {insert,update,delete}`, `--row-id ID`,
  `--new-balance N`.
- `--adapter email`: `--recipient EMAIL` (repeatable), `--subject TEXT`,
  `--body TEXT`.
- `--adapter payment`: `--source-account ID`, `--beneficiary ID`,
  `--amount-minor-units N`, `--currency CODE`, `--reference TEXT`,
  `--fee-minor-units N`, `--fund-source-account N` (credits the manifest
  source account before the effect, for local testing). On `execute`,
  `--fund-account-id` must match that source account; omit it and the
  command uses the account sealed in the manifest. A mismatch errors
  before commit.

**Payment simulator state** is stored in
`<workspace>/payment-simulator.json`, so `verify` in a later process can
see a payment committed earlier. If `prepare` already saved the balance
that the seal fingerprinted, `execute` does not add
`--fund-source-account` on top of it. The email sandbox is still
process-local. SQLite persists in its own database file.

## `assess`

Runs the deterministic Effect Intelligence Engine over a prepared (sealed
or unsealed) manifest and records an `effect.assessed` audit event. Does
not require the manifest to be sealed first, and does not transition the
lifecycle state. **Advisory only** — the recommendation is not read or
enforced by `grant issue`/`execute`. See
[docs/effect-intelligence.md](effect-intelligence.md). `--from-audit-history`
derives the recurrence/failure facts from this workspace's own audit
journal instead of the `--historical-*` flags.

## `policy create` / `sign` / `verify`

Builds, signs, and verifies a signed `PolicyBundle` wrapping an
`IntelligencePolicy` (see [docs/policy-bundles.md](policy-bundles.md)).
`--policy-bundle-id` on `grant issue` binds the sealed bundle's hash into
the issued grant; the same bundle must then be passed to `execute
--policy-bundle-id` or the commit fails closed
(`PolicyBundleMismatchError`) -- this is what prevents a policy edit
between approval and execution from silently changing what was approved.

## `policy create-approval`, `approve`, `approvals inspect`, `grant issue-with-quorum`

Multi-party (M-of-N) authorization (see
[docs/multi-party-authorization.md](multi-party-authorization.md)):
`policy create-approval` builds a quorum-rules bundle (reuse `policy
sign`/`policy verify` on it -- they're generic across bundle types);
`approve` signs one approval or dissent statement for a manifest;
`approvals inspect` dry-runs quorum evaluation without issuing a grant;
`grant issue-with-quorum` issues a grant only if the statements submitted
so far satisfy the bound policy's quorum, failing closed
(`QuorumNotMetError`, non-zero exit) otherwise.

## `policy create-separation`, `--separation-policy-bundle-id`/`--role` on `grant issue*`

Separation of duties (see
[docs/separation-of-duties.md](separation-of-duties.md)):
`policy create-separation` builds a forbidden-role-pair matrix bundle
(default matrix if `--forbidden-pair` is omitted; reuse `policy
sign`/`policy verify` on it). `--separation-policy-bundle-id` on `grant
issue`/`grant issue-with-quorum` enforces that matrix against the
auto-derived proposer/executor/approver(s) plus any `--role
role_name:principal_id` entries, failing closed
(`SeparationOfDutyViolationError`, non-zero exit) if any principal holds
two forbidden roles.

## `evidence-pack build` / `evidence-pack verify`

Portable Evidence Packs (see [docs/portable-evidence.md](portable-evidence.md)):
`evidence-pack build` assembles a self-contained, offline-verifiable
bundle (Action Passport V2 + sealed manifest + grant + audit slice +
public keys) for one manifest, from the same workspace state `passport`
reads. `evidence-pack verify <pack_file>` reads **only** that file — no
workspace, keys, or stores are consulted — and independently re-checks
every embedded signature and hash, exiting non-zero if anything fails to
verify.

## `agenteval record` / `agenteval history`

AgentEval failure-memory loop (see
[docs/agenteval-integration.md](agenteval-integration.md)):
`agenteval record` exports the manifest's outcome as a regression fixture
(same format as the demo's export) and appends it to this workspace's
`agenteval-memory.jsonl`, reporting how many times a failure of this
exact shape (`effect_type` + `adapter_id` + `failure_category` +
`invariant`) has been seen before. `agenteval history` summarizes every
distinct recorded shape, most recurrent first. Advisory only — nothing
here affects any authorization or commit decision.

## `doctor`

Checks workspace initialization, key availability (counts and ids only —
never key material), grant store reachability, audit chain integrity,
clock timezone-awareness, and adapter registration. Exits non-zero if any
check fails.

## `demo --all`

Runs all 15 required scenarios deterministically, self-contained (its own
in-memory engine, its own signing keys, its own temp-file SQLite adapter)
— does not touch `--workspace`. See the README for exact output from a
real run.

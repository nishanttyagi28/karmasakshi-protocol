# FastAPI Control Plane

Optional (`pip install karmasakshi-protocol[api]`). `karmasakshi.api.create_app()`
builds a FastAPI app; `karmasakshi.web.console` adds a server-rendered
local console at `/console/`.

## Running it

```python
from karmasakshi.api import create_app
import uvicorn

app = create_app()  # local dev: wires up the 3 reference adapters automatically
uvicorn.run(app, host="127.0.0.1", port=8000)
```

For production, pass your own `ApiState` (real keys, real adapters) rather
than relying on `build_default_state()`, which is meant for local
development and demos.

## Authentication

**Fail closed by default.** Every route except `/health`, `/ready`, and
`POST /evidence-pack/verify` requires either:

- `KARMASAKSHI_API_DEV_MODE=1` set explicitly (unauthenticated local dev —
  the OpenAPI description and the console's base template both label this
  visibly), **or**
- `KARMASAKSHI_API_TOKEN=<token>` set, and every request carrying
  `Authorization: Bearer <token>`.

If dev mode is off and `KARMASAKSHI_API_TOKEN` is unset, the server
returns `500` on every authenticated route rather than silently serving
without authentication — see `karmasakshi.api.auth.require_auth` and
`tests/integration/test_api.py::TestAuthEnforcement`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, no auth |
| GET | `/ready` | Readiness: dev-mode flag, kill-switch state, audit chain status |
| POST | `/principals` | Register a principal |
| GET | `/principals` | List registered principals |
| POST | `/manifests/prepare` | Prepare + seal a manifest via a reference adapter |
| GET | `/manifests` | List all sealed manifests + lifecycle state |
| GET | `/manifests/{id}` | Inspect one manifest + its seal + grant ids |
| POST | `/manifests/{id}/assess` | Run the Effect Intelligence Engine, record `EffectAssessment` (advisory only -- see docs/effect-intelligence.md) |
| GET | `/manifests/{id}/assessment` | Fetch the most recent assessment recorded for a manifest |
| POST | `/manifests/{id}/approve` | Issue an `ExecutionGrant` (invariant #30 still enforced); optional `policy_bundle_id` binds a signed policy bundle's hash into the grant; optional `separation_policy_bundle_id`/`roles` enforce separation of duties (`403` on violation); optional `decision_envelope_id` *or* `causal_graph_id` (mutually exclusive) binds Phase 6 plan-level authorization |
| POST | `/manifests/{id}/deny` | Audit-log a denial; never issues a grant |
| POST | `/decision-envelopes` | Build, seal, and store a constrained Decision Envelope (see docs/decision-envelopes.md) |
| GET | `/decision-envelopes/{id}` | Fetch a stored Decision Envelope and verification status |
| POST | `/decision-envelopes/{id}/substitute` | Deterministically resolve parameter choices under a sealed envelope |
| POST | `/manifests/{id}/compensation/prepare` | Build and seal a compensation manifest bound to the original (Phase 7) |
| POST | `/manifests/{id}/compensation/{cid}/authorize` | Issue a separate grant for the compensation effect |
| POST | `/manifests/{id}/compensation/{cid}/execute` | Consume the compensation grant and call `adapter.compensate` |
| GET | `/manifests/{id}/compensation/{cid}/passport` | Emit a Compensation Passport (never mutates the Action Passport) |
| POST | `/policy/bundles` | Build, sign, and store a policy bundle (see docs/policy-bundles.md) |
| GET | `/policy/bundles/{id}` | Fetch a stored sealed policy bundle |
| POST | `/policy/bundles/{id}/verify` | Re-verify a stored bundle's signature/integrity/window |
| POST | `/policy/approval-bundles` | Build, sign, and store an approval (quorum) policy bundle (see docs/multi-party-authorization.md) |
| POST | `/policy/separation-bundles` | Build, sign, and store a separation-of-duty policy bundle (see docs/separation-of-duties.md) |
| POST | `/manifests/{id}/approvals` | Record one approval/dissent statement (signed by the control plane's own key -- see the "Honesty note" in docs/multi-party-authorization.md) |
| GET | `/manifests/{id}/approvals` | List statements recorded for a manifest |
| POST | `/manifests/{id}/approvals/evaluate` | Dry-run quorum evaluation, does not issue a grant |
| POST | `/manifests/{id}/witnesses` | Record one independent witness statement (Phase 9; see docs/witness-quorum.md) |
| GET | `/manifests/{id}/witnesses` | List witness statements for a manifest |
| POST | `/manifests/{id}/witnesses/evaluate` | Evaluate / optionally assert independent witness quorum |
| POST | `/manifests/{id}/approve-with-quorum` | Issue a grant if quorum is met (`403` if not); same optional `separation_policy_bundle_id`/`roles` fields as `/approve` |
| POST | `/grants/{id}/revoke` | Revoke a grant |
| POST | `/manifests/{id}/execute` | Commit (blocked with `503` if the kill switch is engaged); if the grant is policy-bundle / Decision-Envelope / causal-graph-bound, the matching artifact id must be supplied or the commit fails closed |
| POST | `/manifests/{id}/verify` | Independently verify the outcome |
| GET | `/audit` | Full audit timeline |
| GET | `/audit/verify` | Verify the hash chain |
| GET | `/passports/{id}?fmt=json\|html\|markdown&version=v1\|v2` | Action Passport (v1 default; v2 = schema 2.0) |
| GET | `/passports/{id}/evidence-pack` | Build a portable, offline-verifiable Evidence Pack (Phase 24; see docs/portable-evidence.md) |
| POST | `/evidence-pack/verify` | Independently (fully offline) verify a submitted Evidence Pack -- deliberately unauthenticated |
| POST | `/manifests/{id}/agenteval/fixtures` | Export + record an AgentEval regression fixture into the failure-memory store (Phase 25; advisory only) |
| GET | `/agenteval/fixtures/history` | Summarize recorded failure signatures, most recurrent first |
| GET | `/kill-switch` | Current status |
| POST | `/kill-switch/engage` / `/disengage` | Emergency stop for `/execute` |

Full request/response schemas: `karmasakshi.api.schemas`, or the live
OpenAPI JSON at `/openapi.json` (interactive docs at `/docs`).

## Local console

`/console/` — dashboard (pending approvals, kill switch, audit integrity),
`/console/manifests/{id}` (before/after fields, approve/deny HTML forms),
`/console/grants` (active/revoked, delegation lineage via the Parent
column), `/console/audit` (timeline), `/console/workflows` and
`/console/workflows/{id}` (read-only handoff tree, passport outcome, and
evidence-pack check for the CLI workspace at `.karmasakshi` or
`KARMASAKSHI_HOME`). Plain server-rendered HTML, no
JavaScript build step — forms POST directly to console routes, which share
the same auth dependency as the JSON API.

## State model

`ApiState` is process-local (see `docs/storage-semantics.md` for the
underlying store/audit backends it wires up). There is no built-in
horizontal scaling story beyond what a shared Redis grant store + shared
audit backend would require — this control plane is a reference
implementation, not a scaled-out production service.

# KarmaSakshi Protocol

Seal what an AI agent was supposed to do. Witness what actually happened.

[![CI](https://github.com/nishanttyagi28/karmasakshi-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/nishanttyagi28/karmasakshi-protocol/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/badge/pypi-karmasakshi--protocol-blue.svg)](https://pypi.org/project/karmasakshi-protocol/)

AI agents can send money, change records, and call external systems.

Most permission tools only answer: *may this agent call this tool?* They do not prove that the exact thing a person approved is the exact thing that ran — once — and that the outside world really ended up in that state.

**KarmaSakshi Protocol** is a small runtime I built to close that gap: lock the intended effect, require a human (or other non-agent) approval for that exact effect, run it carefully, then check the real outcome and keep a record.

The name is literal. *Karma* means action; *Sakshi* means witness.

**Status:** v0.2.0 · experimental · evaluation-ready for self-hosted demos. Not production-proven, not audited, not a real bank or mail provider. Simulators and reference adapters only.

---

## The problem

Imagine finance approved a ₹1,500 refund to customer Priya for order 8842.

Without something like this, an agent that is allowed to call `payment.transfer` can still:

- Change the recipient or amount after approval
- Retry and pay twice after a timeout
- Run against an account balance that already changed
- Report “success” when the ledger says something else

None of that needs a malicious model. A retry loop or a stale row is enough.

---

## In simple terms

If a recruiter asks “so what does this project actually do?”, here’s how I’d answer.

Suppose an agent was supposed to refund **₹1,500 to Priya**.

After approval, the agent (or a buggy retry) tries to send **₹1,501 to a different account**.

KarmaSakshi treats that as a different action. The approval was for the sealed ₹1,500→Priya effect, so the changed attempt is blocked. If the correct refund does run, the system looks at the payment ledger again and records whether the money actually moved as approved — not only whether the tool call returned OK.

That’s the idea: **seal intended effect → approve that exact effect → execute once → witness the real outcome.**

It sits *after* normal identity and “is this tool allowed?” checks. It does not replace them.

---

## What I built

### Exact effect, not just a tool name

Before anything runs, the proposed action is turned into a precise description: who, what, how much, expected before/after state. That description is sealed (hashed and signed) so a later edit is a different object.

```bash
karmasakshi prepare --adapter payment ...
karmasakshi seal <manifest-id> --key-id issuer-1
```

**Why it helps:** Approval is tied to one concrete refund (or email, or row change), not to a vague “this agent may transfer money.”

### Human approval bound to that seal

A grant can only authorize the sealed effect it was issued for. The agent cannot issue its own approval.

```bash
karmasakshi grant issue <manifest-id> \
  --issuer-id finance-approver --subject-id refund-agent --key-id issuer-1
```

**Why it helps:** Swapping the recipient after someone clicked approve fails closed instead of silently succeeding.

### Check again at commit time, run at most once

Right before execution, preconditions are re-checked. A single-use grant is reserved so concurrent retries race for one successful run.

```bash
karmasakshi execute <manifest-id> --grant-id <grant-id> --adapter payment ...
```

**Why it helps:** Stale balances and double refunds are treated as failures, not “probably fine.”

### Independent outcome check + Action Passport

After commit, the adapter re-reads external state. A passport records the chain: proposed → approved → committed → verified (or mismatch).

```bash
karmasakshi verify <manifest-id> --adapter payment ...
karmasakshi passport <manifest-id>
```

**Why it helps:** “API said OK” is not treated as proof that the ledger matches what was approved.

### Self-contained demos you can run

```bash
pip install karmasakshi-protocol
karmasakshi init
karmasakshi key generate issuer-1
karmasakshi demo --all
```

`demo --all` walks 15 security scenarios against the real engine and three reference adapters (payment simulator, email sandbox, SQLite rows). No real money, no real email.

There is also a browser Control Center and a 25-check buyer acceptance command for the self-hosted refund journey — see below.

---

## A few numbers

Things you can verify in this repo (verified against current `main`):

| | |
| --- | --- |
| Automated tests | **1073 passed**, 8 skipped (Redis tests skip without a live Redis) |
| Security invariants documented + mapped to tests | **85** ([docs/security-model.md](docs/security-model.md)) |
| Deterministic demo scenarios | **15** (`karmasakshi demo --all`) |
| Buyer acceptance checks | **25** (`karmasakshi-acceptance`) |
| Reference adapters | **3** (payment simulator, email sandbox, SQLite) |
| Python package source files | **191** |
| Docs pages under `docs/` | **62** |
| Python | 3.10–3.13 |
| License | MIT |
| Version | 0.2.0 (experimental) |

I’m not claiming production adoption percentages or “X% safer.” Those numbers aren’t in this repo.

---

## Why this matters

Allowing a tool call and proving an approved real-world effect are different jobs.

If agents are going to move money or change records, someone eventually asks: *did the thing we approved actually happen, once, to the right target?* This project is one concrete attempt to make that question answerable with code and an audit trail — not with a slide deck.

---

## How it works

```text
Agent proposes an action
        ↓
Prepare exact effect description
        ↓
Seal it (hash + signature)
        ↓
Human / service approves that seal
        ↓
Re-check state, execute at most once
        ↓
Independently observe the real outcome
        ↓
Action Passport + hash-chained audit log
```

Trust boundary in one line: the agent may propose; it does not hold the signing authority that issues grants.

Details for engineers are below (install, CLI, API, docs).

---

## Evaluate locally (Docker)

```bash
docker compose up --detach --build --wait api
docker compose --profile acceptance run --rm acceptance
```

That prints 25 `PASS` checks and local credentials. Open `http://127.0.0.1:8000/control-center/login`. When finished: `docker compose down --volumes`.

Control Center demo video: [docs/assets/control-center/control-center-demo.mp4](docs/assets/control-center/control-center-demo.mp4).

Without Docker:

```bash
# terminal 1
pip install "karmasakshi-protocol[api]"
KARMASAKSHI_API_DEV_MODE=1 \
  python -m uvicorn karmasakshi.api.app:create_app --factory

# terminal 2
karmasakshi-acceptance --base-url http://127.0.0.1:8000 \
  --report artifacts/milestone-a-acceptance.json
```

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install karmasakshi-protocol
karmasakshi init
karmasakshi key generate issuer-1
karmasakshi demo --all
```

Optional extras:

```bash
pip install "karmasakshi-protocol[api]"        # FastAPI control plane + Control Center
pip install "karmasakshi-protocol[langgraph]"  # optional LangGraph helper
pip install "karmasakshi-protocol[redis]"      # distributed grant store
pip install "karmasakshi-protocol[all]"
```

Public sandbox (simulators only, rate-limited):

```bash
pip install "karmasakshi-protocol[api]"
KARMASAKSHI_PUBLIC_DEMO=1 python -m uvicorn karmasakshi.api.app:create_app --factory
# open http://127.0.0.1:8000/demo/
```

---

## CLI

Main flow:

```text
karmasakshi init
karmasakshi key generate|list
karmasakshi prepare | seal | assess | approve
karmasakshi grant issue|verify|delegate|revoke|inspect
karmasakshi execute | verify | compensate | passport
karmasakshi demo --all
karmasakshi doctor
karmasakshi audit list|show|verify
```

Also: `graph`, `envelope`, `policy`, `approvals`, `witness`, `evidence-pack`, `compensation`, `agenteval`, `handoff`, `workflow`.

Full reference: [docs/cli.md](docs/cli.md).

### Short SQLite walkthrough

```bash
karmasakshi prepare --adapter sqlite --actor-id refund-agent \
  --sqlite-db-path ledger.db --sqlite-table refunds \
  --row-operation insert --row-id refund-8842 --new-balance 150000
karmasakshi seal <manifest-id> --key-id issuer-1
karmasakshi grant issue <manifest-id> --issuer-id finance-approver \
  --subject-id refund-agent --key-id issuer-1 --audience sqlite.row
karmasakshi execute <manifest-id> --grant-id <grant-id> \
  --adapter sqlite --sqlite-db-path ledger.db --sqlite-table refunds
karmasakshi verify <manifest-id> --adapter sqlite \
  --sqlite-db-path ledger.db --sqlite-table refunds
karmasakshi passport <manifest-id>
```

---

## Python sketch

```python
manifest = engine.prepare(adapter, request, context=None)
sealed = engine.seal(manifest, signing_key)
grant = engine.authorize(
    sealed,
    issuer=human,  # never the agent
    subject=agent,
    audience=("payment.simulator",),
    allowed_effect_types=("payment.transfer",),
    scope=ScopeConstraints(recipients=("customer-priya",)),
    not_before=now,
    expires_at=now + timedelta(minutes=5),
    signing_key=signing_key,
)
result = engine.commit(sealed, grant, adapter, context=None)
proof = engine.verify(sealed.manifest, result, adapter, context=None)
# result.success and proof.matched_expected tell you commit vs real outcome
```

A longer refund example and failure modes live in the older technical write-ups under `docs/`.

---

## Multi-agent handoff

A planner can pass work to another agent only as a narrower grant. The receiver checks the envelope (hash, signature, chain, revocation, expiry, addressee) before it acts. The human or service key still issues every grant. An agent cannot.

```bash
pip install -e ".[langgraph]"
python examples/multi_agent_handoff/run_workflow.py
karmasakshi workflow export wf-refund-8842 --workspace .karmasakshi
```

That run is offline: three fixed agents, one simulated payment, passports, and one evidence pack. The script writes the workflow into `./.karmasakshi` so the export command above can verify it. A CLI-only walkthrough of the same flow is in [docs/multi-agent-handoff.md](docs/multi-agent-handoff.md).

```text
karmasakshi handoff create|accept|inspect
karmasakshi workflow export <workflow-id>
karmasakshi workflow verify <file>
karmasakshi execute <manifest> --workflow-id ID --handoff-id ID
```

---

## Screenshots

| | |
|---|---|
| ![Sandbox landing](docs/assets/screenshots/01-landing-overview.png) **Sandbox overview** | ![Pending approval](docs/assets/screenshots/02-pending-effect-approval.png) **Pending approval** |
| ![Before/after diff](docs/assets/screenshots/03-effect-manifest-before-after-diff.png) **Exact before/after** | ![Verified execution](docs/assets/screenshots/04-successful-verified-execution.png) **Verified execution** |
| ![Blocked tampering](docs/assets/screenshots/05-blocked-tampering-attempt.png) **Blocked: changed after approval** | ![Action Passport](docs/assets/screenshots/09-action-passport.png) **Action Passport** |

Protocol demo video (agent proposes refund → approve → verify → tamper blocked): [docs/assets/demo/demo.mp4](docs/assets/demo/demo.mp4).

---

## Documentation

Start here if you want depth:

- [docs/architecture.md](docs/architecture.md) — components and data flow
- [docs/security-model.md](docs/security-model.md) — 85 invariants ↔ code ↔ tests
- [docs/multi-agent-handoff.md](docs/multi-agent-handoff.md) — agents pass a narrowed grant, then a passport
- [docs/threat-model.md](docs/threat-model.md) — what is and isn’t defended
- [docs/limitations.md](docs/limitations.md) — honest limits
- [docs/cli.md](docs/cli.md) · [docs/api.md](docs/api.md) · [docs/sdk.md](docs/sdk.md)
- [docs/gateway.md](docs/gateway.md) · [docs/product/BUYER_EVALUATION.md](docs/product/BUYER_EVALUATION.md)
- [docs/comparison.md](docs/comparison.md) — how this differs from permission layers
- Full index: browse [`docs/`](docs/)

---

## Status

**WIP · v0.2.0 · actively developed.**

Useful today for evaluation, demos, and local experiments. Adapters are reference/simulators — not Stripe, SendGrid, or a production database product. No third-party security audit. Schemas and APIs may still move; pin a commit if you depend on specific behavior. I’m not calling this a finished product.

## License

MIT. See [LICENSE](LICENSE).

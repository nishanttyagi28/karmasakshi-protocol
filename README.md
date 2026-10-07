# KarmaSakshi Protocol

A Python library and CLI that ties an approval to one exact agent action, runs it at most once, and then checks that the real outcome matches what was approved.

[![CI](https://github.com/nishanttyagi28/karmasakshi-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/nishanttyagi28/karmasakshi-protocol/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/karmasakshi-protocol.svg)](https://pypi.org/project/karmasakshi-protocol/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Most permission checks answer "may this agent call `payment.transfer`?". They don't check that the transfer which runs is the one a person approved, that it ran only once, or that the ledger actually changed. Say finance approves a ₹1,500 refund to Priya. A retry loop, a stale row, or a changed recipient can still turn that into ₹1,501 to someone else, or into two refunds, without any malicious model involved.

KarmaSakshi works in five steps:

1. Turn the proposed action into a precise description (target, amount, expected before/after state).
2. Hash and sign that description, so any later edit makes it a different object.
3. Issue a grant for that exact description. Grants are signed by a human or service key, never by the agent.
4. Re-check preconditions right before running, and consume the grant so concurrent retries can't run it twice.
5. Read the external state again afterwards and record whether it matches what was approved.

The name means "action" (*karma*) and "witness" (*sakshi*).

## Install

Python 3.10 to 3.13.

```bash
pip install karmasakshi-protocol
```

Extras: `api` (FastAPI server and browser UI), `langgraph`, `redis` (shared grant store), `sdk`, or `all`.

## Quick start

```bash
karmasakshi init                     # creates ./.karmasakshi (keys, manifests, grants)
karmasakshi key generate issuer-1    # local Ed25519 signing key
karmasakshi demo --all
```

`demo --all` runs 15 scenarios against the real engine and the bundled simulators: a changed recipient after approval, an expired or revoked grant, concurrent payment retries, a tampered audit log, an outcome that doesn't match, and so on. Each one prints `PASS` when it is blocked or detected as expected. No real money or email is involved.

The same flow by hand, using the SQLite adapter:

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

`prepare`, `seal` and `grant issue` print the IDs to use in the next step. Running `execute` a second time with the same grant is refused. `passport` prints a Markdown summary of what was proposed, approved, executed and observed (the project calls this an "Action Passport").

## Features

- Three reference adapters: a payment simulator, an email sandbox, and SQLite rows. See [docs/adapter-authoring.md](docs/adapter-authoring.md) to write your own.
- Hash-chained audit log with `karmasakshi audit verify`.
- Delegation: a grant can be passed on only in a narrower form, and revoking a parent blocks its children.
- Best-effort compensation for reversible effects. Irreversible ones, like a sent email, refuse instead of pretending to roll back.
- Multi-agent handoff: one agent passes work to another as a signed, narrower grant. Offline example: `python examples/multi_agent_handoff/run_workflow.py` (needs the `langgraph` extra).
- An HTTP API and a browser control panel for approvals and audit (`api` extra). See [docs/control-center.md](docs/control-center.md).
- Integration with [AgentEval](https://github.com/nishanttyagi28/agenteval), so blocked attempts are recorded as test failures. See [docs/agenteval-integration.md](docs/agenteval-integration.md).

Full command reference: [docs/cli.md](docs/cli.md). Python API: [docs/sdk.md](docs/sdk.md) and [docs/api.md](docs/api.md).

## Running the API locally

```bash
docker compose up --detach --build --wait api
docker compose --profile acceptance run --rm acceptance
```

The acceptance run prints a list of `PASS` checks for the refund flow and the local login details. Then open `http://127.0.0.1:8000/control-center/login`. Run `docker compose down --volumes` when you're done.

Without Docker:

```bash
pip install "karmasakshi-protocol[api]"
KARMASAKSHI_API_DEV_MODE=1 python -m uvicorn karmasakshi.api.app:create_app --factory
```

## Limitations

This is an experimental alpha (v0.2.0). In particular:

- It has not had a security audit and hasn't run in a real deployment.
- The adapters are simulators and reference implementations, not connectors to Stripe, SendGrid or a real database.
- SQLite storage is single-machine. Redis is available for grants, but the Redis tests skip unless a Redis server is reachable.
- Signing keys are local files. There is no cloud KMS or HSM support.
- Schemas and APIs may still change.

More detail in [docs/limitations.md](docs/limitations.md), [docs/threat-model.md](docs/threat-model.md) and [docs/security-model.md](docs/security-model.md).

## Development

```bash
git clone https://github.com/nishanttyagi28/karmasakshi-protocol.git
cd karmasakshi-protocol
pip install -e ".[all]" pytest pytest-asyncio pytest-cov hypothesis freezegun
python -m pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [docs/architecture.md](docs/architecture.md) and [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).

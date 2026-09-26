"""Resolves the CLI's ``--adapter`` choice to a concrete reference adapter.

Only the three reference adapters ship with the CLI; third-party adapters
are wired programmatically via the Python API (see docs/adapter-authoring.md),
not dynamically loaded from CLI strings, to avoid an arbitrary-code-loading
surface in a security-focused CLI.

Note: the email adapter holds its outbox in memory, so a fresh CLI
process starts it empty. The payment simulator can be restored from a
snapshot (the CLI stores one in the workspace). Only the SQLite adapter
persists through its own database file. Use ``karmasakshi demo`` to see a
full single-process walkthrough of all three.
"""

from __future__ import annotations

from typing import Annotated

import typer

from karmasakshi.adapters.base import EffectAdapter
from karmasakshi.adapters.email_sandbox import EmailRequest, EmailSandboxAdapter, SandboxOutbox
from karmasakshi.adapters.payment_simulator import (
    PaymentRequest,
    PaymentSimulator,
    PaymentSimulatorAdapter,
)
from karmasakshi.adapters.sqlite_db import RowEffectRequest, RowOperation, SQLiteRowAdapter
from karmasakshi.domain.common import Principal

AdapterChoice = Annotated[str, typer.Option("--adapter", help="One of: sqlite, email, payment")]


def build_adapter(
    choice: str,
    *,
    sqlite_db_path: str | None,
    sqlite_table: str,
    fund_source_account: int | None,
    fund_account_id: str | None = None,
    payment_simulator: PaymentSimulator | None = None,
) -> EffectAdapter:
    if choice == "sqlite":
        if not sqlite_db_path:
            raise ValueError("--sqlite-db-path is required for the sqlite adapter")
        return SQLiteRowAdapter(sqlite_db_path, table=sqlite_table)
    if choice == "email":
        return EmailSandboxAdapter(SandboxOutbox())
    if choice == "payment":
        simulator = payment_simulator if payment_simulator is not None else PaymentSimulator()
        if fund_source_account is not None:
            if not fund_account_id:
                raise ValueError("fund_account_id is required when --fund-source-account is set")
            simulator.fund_account(fund_account_id, fund_source_account)
        return PaymentSimulatorAdapter(simulator)
    raise ValueError(f"unknown adapter choice: {choice!r} (expected sqlite, email, or payment)")


def build_request(
    choice: str,
    *,
    actor: Principal,
    principal: Principal,
    idempotency_key: str,
    ttl_seconds: int,
    row_operation: RowOperation | None,
    row_id: str | None,
    new_balance: int | None,
    recipients: list[str],
    subject: str | None,
    body: str | None,
    source_account: str | None,
    beneficiary: str | None,
    amount_minor_units: int | None,
    currency: str,
    reference: str | None,
    fee_minor_units: int,
) -> RowEffectRequest | EmailRequest | PaymentRequest:
    if choice == "sqlite":
        if not row_operation or not row_id:
            raise ValueError("--row-operation and --row-id are required for the sqlite adapter")
        return RowEffectRequest(
            operation=row_operation,
            row_id=row_id,
            actor=actor,
            principal=principal,
            new_balance=new_balance,
            idempotency_key=idempotency_key,
            ttl_seconds=ttl_seconds,
        )
    if choice == "email":
        if not recipients or not subject or body is None:
            raise ValueError(
                "--recipient, --subject, and --body are required for the email adapter"
            )
        return EmailRequest(
            actor=actor,
            principal=principal,
            recipients=tuple(recipients),
            subject=subject,
            body=body,
            idempotency_key=idempotency_key,
            ttl_seconds=ttl_seconds,
        )
    if choice == "payment":
        if not source_account or not beneficiary or amount_minor_units is None or not reference:
            raise ValueError(
                "--source-account, --beneficiary, --amount-minor-units, and --reference "
                "are required for the payment adapter"
            )
        return PaymentRequest(
            actor=actor,
            principal=principal,
            source_account=source_account,
            beneficiary=beneficiary,
            amount_minor_units=amount_minor_units,
            currency=currency,
            reference=reference,
            fee_minor_units=fee_minor_units,
            idempotency_key=idempotency_key,
            ttl_seconds=ttl_seconds,
        )
    raise ValueError(f"unknown adapter choice: {choice!r} (expected sqlite, email, or payment)")


__all__ = ["AdapterChoice", "build_adapter", "build_request"]

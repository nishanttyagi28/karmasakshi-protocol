"""Snapshot round-trip for the payment simulator."""

from __future__ import annotations

import pytest

from karmasakshi.adapters.payment_simulator import PaymentSimulator


def test_snapshot_restores_balance_and_settled_payment():
    simulator = PaymentSimulator()
    simulator.fund_account("operating", 10_000)
    simulator.submit_payment(
        provider_idempotency_key="idem-1",
        source_account="operating",
        beneficiary="priya",
        amount_minor_units=1500,
        currency="INR",
        fee_minor_units=0,
        reference="ref-1",
    )
    restored = PaymentSimulator.restore(simulator.snapshot())
    assert restored.get_balance("operating") == 8500
    record = restored.get_payment("idem-1")
    assert record is not None
    assert record.status == "settled"
    assert record.beneficiary == "priya"


def test_restore_rejects_a_malformed_snapshot():
    with pytest.raises(ValueError):
        PaymentSimulator.restore({"balances": {"operating": "nope"}, "payments": []})

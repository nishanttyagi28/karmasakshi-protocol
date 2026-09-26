"""Deterministic payment provider simulator adapter.

Never moves real money. :class:`PaymentSimulator` models a minimal provider
with its own account ledger and idempotency key tracking, including a
deliberately-supported "ambiguous timeout" failure mode: the provider
*records the payment as settled internally* but the call that submitted it
raises :class:`TimeoutError`, exactly the crash-recovery scenario described
in docs/crash-recovery.md. The adapter never trusts its own commit result
for verification -- :meth:`PaymentSimulatorAdapter.verify` re-queries the
simulator by idempotency key.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from karmasakshi.adapters.base import (
    CommitResult,
    CompensationResult,
    OutcomeProof,
    PreconditionResult,
)
from karmasakshi.domain.common import AdapterIdentity, MonetaryAmount, Principal, StateFingerprint
from karmasakshi.domain.enums import (
    BlastRadiusClassification,
    ReversibilityClassification,
    RiskClassification,
    StateFingerprintKind,
)
from karmasakshi.domain.manifest import EffectManifest

PaymentStatus = Literal["settled", "failed"]


def _int_param(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError(f"expected an int manifest parameter, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class PaymentRecord:
    provider_idempotency_key: str
    beneficiary: str
    amount_minor_units: int
    currency: str
    fee_minor_units: int
    status: PaymentStatus
    reference: str


@dataclass
class PaymentRequest:
    actor: Principal
    principal: Principal
    source_account: str
    beneficiary: str
    amount_minor_units: int
    currency: str
    reference: str
    fee_minor_units: int = 0
    idempotency_key: str = ""
    ttl_seconds: int = 300


class PaymentSimulator:
    """A deterministic in-memory payment provider."""

    def __init__(self) -> None:
        self.balances: dict[str, int] = {}
        self._payments: dict[str, PaymentRecord] = {}
        self._fail_next = False
        self._timeout_next = False

    def fund_account(self, account: str, minor_units: int) -> None:
        self.balances[account] = self.balances.get(account, 0) + minor_units

    def get_balance(self, account: str) -> int:
        return self.balances.get(account, 0)

    def snapshot(self) -> dict[str, Any]:
        """Balances and payment records, stable enough to write to disk."""
        return {
            "balances": {account: self.balances[account] for account in sorted(self.balances)},
            "payments": [asdict(self._payments[key]) for key in sorted(self._payments)],
        }

    @classmethod
    def restore(cls, data: dict[str, Any]) -> PaymentSimulator:
        """Rebuild a simulator from :meth:`snapshot`.

        Rejects a malformed snapshot instead of dropping payments. A later
        ``verify`` would otherwise report a mismatch for a payment that did
        settle.
        """
        if not isinstance(data, dict):
            raise ValueError("payment simulator snapshot must be an object")
        balances = data.get("balances", {})
        payments = data.get("payments", [])
        if not isinstance(balances, dict) or not isinstance(payments, list):
            raise ValueError("payment simulator snapshot has the wrong shape")
        simulator = cls()
        for account, amount in balances.items():
            if not isinstance(account, str) or not account:
                raise ValueError("payment simulator snapshot account id is invalid")
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise ValueError(f"payment simulator balance for {account!r} is not an int")
            simulator.balances[account] = amount
        for item in payments:
            if not isinstance(item, dict):
                raise ValueError("payment simulator snapshot payment is not an object")
            record = PaymentRecord(
                provider_idempotency_key=str(item["provider_idempotency_key"]),
                beneficiary=str(item["beneficiary"]),
                amount_minor_units=item["amount_minor_units"],
                currency=str(item["currency"]),
                fee_minor_units=item["fee_minor_units"],
                status=item["status"],
                reference=str(item["reference"]),
            )
            if record.status not in ("settled", "failed"):
                raise ValueError(f"unknown payment status {record.status!r}")
            if isinstance(record.amount_minor_units, bool) or not isinstance(
                record.amount_minor_units, int
            ):
                raise ValueError("payment amount_minor_units must be an int")
            if isinstance(record.fee_minor_units, bool) or not isinstance(
                record.fee_minor_units, int
            ):
                raise ValueError("payment fee_minor_units must be an int")
            simulator._payments[record.provider_idempotency_key] = record
        return simulator

    def inject_failure(self) -> None:
        """Next submit_payment() call returns a failed payment record."""
        self._fail_next = True

    def inject_ambiguous_timeout(self) -> None:
        """Next submit_payment() call settles internally but raises TimeoutError."""
        self._timeout_next = True

    def get_payment(self, provider_idempotency_key: str) -> PaymentRecord | None:
        return self._payments.get(provider_idempotency_key)

    def submit_payment(
        self,
        *,
        provider_idempotency_key: str,
        source_account: str,
        beneficiary: str,
        amount_minor_units: int,
        currency: str,
        fee_minor_units: int,
        reference: str,
    ) -> PaymentRecord:
        existing = self._payments.get(provider_idempotency_key)
        if existing is not None:
            return existing  # provider-level idempotency; never double-charge

        if self._fail_next:
            self._fail_next = False
            record = PaymentRecord(
                provider_idempotency_key=provider_idempotency_key,
                beneficiary=beneficiary,
                amount_minor_units=amount_minor_units,
                currency=currency,
                fee_minor_units=fee_minor_units,
                status="failed",
                reference=reference,
            )
            self._payments[provider_idempotency_key] = record
            return record

        total = amount_minor_units + fee_minor_units
        if self.balances.get(source_account, 0) < total:
            record = PaymentRecord(
                provider_idempotency_key=provider_idempotency_key,
                beneficiary=beneficiary,
                amount_minor_units=amount_minor_units,
                currency=currency,
                fee_minor_units=fee_minor_units,
                status="failed",
                reference=reference,
            )
            self._payments[provider_idempotency_key] = record
            return record

        self.balances[source_account] -= total
        record = PaymentRecord(
            provider_idempotency_key=provider_idempotency_key,
            beneficiary=beneficiary,
            amount_minor_units=amount_minor_units,
            currency=currency,
            fee_minor_units=fee_minor_units,
            status="settled",
            reference=reference,
        )
        self._payments[provider_idempotency_key] = record

        if self._timeout_next:
            self._timeout_next = False
            # The payment settled provider-side, but the caller never learns
            # that from this call -- the classic ambiguous-outcome scenario.
            raise TimeoutError("simulated network timeout after settlement")
        return record


class PaymentSimulatorAdapter:
    adapter_id = "payment.simulator"
    adapter_version = "1.0.0"

    def __init__(self, simulator: PaymentSimulator) -> None:
        self.simulator = simulator
        self._identity = AdapterIdentity(
            adapter_id=self.adapter_id, adapter_version=self.adapter_version
        )

    def prepare(self, request: PaymentRequest, context: Any) -> EffectManifest:
        now = datetime.now(timezone.utc)
        balance = self.simulator.get_balance(request.source_account)
        return EffectManifest(
            manifest_id=str(uuid.uuid4()),
            effect_type="payment.transfer",
            actor=request.actor,
            principal=request.principal,
            adapter=self._identity,
            target_resource=f"payment:beneficiary/{request.beneficiary}",
            parameters={
                "source_account": request.source_account,
                "beneficiary": request.beneficiary,
                "amount_minor_units": request.amount_minor_units,
                "currency": request.currency,
                "fee_minor_units": request.fee_minor_units,
                "reference": request.reference,
            },
            state_fingerprint=StateFingerprint(
                kind=StateFingerprintKind.PROVIDER_STATUS, value=f"balance:{balance}"
            ),
            risk=RiskClassification.HIGH,
            reversibility=ReversibilityClassification.COMPENSATABLE,
            blast_radius=BlastRadiusClassification.SINGLE_RESOURCE,
            estimated_cost=MonetaryAmount(
                currency=request.currency,
                minor_units=request.amount_minor_units + request.fee_minor_units,
            ),
            idempotency_key=request.idempotency_key or f"payment-{request.reference}",
            created_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
            nonce=uuid.uuid4().hex,
        )

    def validate_preconditions(self, manifest: EffectManifest, context: Any) -> PreconditionResult:
        assert manifest.state_fingerprint is not None  # nosec B101 - set unconditionally by this adapter's own prepare()
        source_account = str(manifest.parameters["source_account"])
        current_balance = self.simulator.get_balance(source_account)
        expected_balance = int(manifest.state_fingerprint.value.removeprefix("balance:"))
        if current_balance != expected_balance:
            return PreconditionResult(
                satisfied=False,
                reason=(
                    f"source account balance changed since prepare: expected "
                    f"{expected_balance}, observed {current_balance}"
                ),
                observed_fingerprint=StateFingerprint(
                    kind=StateFingerprintKind.PROVIDER_STATUS,
                    value=f"balance:{current_balance}",
                ),
            )
        total = _int_param(manifest.parameters["amount_minor_units"]) + _int_param(
            manifest.parameters["fee_minor_units"]
        )
        if current_balance < total:
            return PreconditionResult(satisfied=False, reason="insufficient balance")
        return PreconditionResult(satisfied=True)

    def commit(self, manifest: EffectManifest, grant: Any, context: Any) -> CommitResult:
        try:
            record = self.simulator.submit_payment(
                provider_idempotency_key=manifest.idempotency_key,
                source_account=str(manifest.parameters["source_account"]),
                beneficiary=str(manifest.parameters["beneficiary"]),
                amount_minor_units=_int_param(manifest.parameters["amount_minor_units"]),
                currency=str(manifest.parameters["currency"]),
                fee_minor_units=_int_param(manifest.parameters["fee_minor_units"]),
                reference=str(manifest.parameters["reference"]),
            )
        except TimeoutError:
            return CommitResult(
                success=False,
                idempotency_key=manifest.idempotency_key,
                detail=(
                    "ambiguous: request timed out, outcome unknown -- call verify() or "
                    "engine.recover_ambiguous_commit() before retrying"
                ),
            )
        if record.status == "failed":
            return CommitResult(
                success=False, idempotency_key=manifest.idempotency_key, detail="payment failed"
            )
        return CommitResult(
            success=True,
            idempotency_key=manifest.idempotency_key,
            provider_reference=record.reference,
            after_state_digest=f"settled:{record.provider_idempotency_key}",
        )

    def verify(
        self, manifest: EffectManifest, commit_result: CommitResult, context: Any
    ) -> OutcomeProof:
        record = self.simulator.get_payment(manifest.idempotency_key)
        if record is None:
            return OutcomeProof(
                matched_expected=False,
                observed_at=datetime.now(timezone.utc),
                detail="no payment record found for this idempotency key",
            )
        matched = (
            record.status == "settled"
            and record.amount_minor_units == _int_param(manifest.parameters["amount_minor_units"])
            and record.beneficiary == str(manifest.parameters["beneficiary"])
        )
        return OutcomeProof(
            matched_expected=matched,
            observed_at=datetime.now(timezone.utc),
            observed_after_state_digest=f"settled:{record.provider_idempotency_key}",
            detail=f"provider status: {record.status}",
        )

    def compensate(
        self, manifest: EffectManifest, commit_result: CommitResult, context: Any
    ) -> CompensationResult:
        record = self.simulator.get_payment(manifest.idempotency_key)
        if record is None or record.status == "failed":
            return CompensationResult(
                attempted=False, succeeded=False, reason="nothing to compensate"
            )
        return CompensationResult(
            attempted=True,
            succeeded=False,
            reason=(
                "settled transfers cannot be reversed by this simulator; a real reversing "
                "payment would be required, and providers do not guarantee one succeeds"
            ),
        )


__all__ = [
    "PaymentRecord",
    "PaymentRequest",
    "PaymentSimulator",
    "PaymentSimulatorAdapter",
]

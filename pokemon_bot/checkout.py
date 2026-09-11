from typing import Protocol

from .models import CheckoutResult, Decision


class Checkout(Protocol):
    def purchase(self, decision: Decision) -> CheckoutResult:
        ...


class DryRunCheckout:
    def purchase(self, decision: Decision) -> CheckoutResult:
        return CheckoutResult(
            success=True,
            status="dry_run",
            detail="Would purchase %d x %s for $%.2f"
            % (decision.rule.quantity, decision.offer.title, decision.total_cents / 100),
        )

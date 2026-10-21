import unittest
from decimal import Decimal

from withdrawal_sizing import desired_position


def snapshot():
    return {"evidence": "verified-public-reads", "blockTimestamp": 100,
            "operationInProgress": False, "operationPhase": 0, "restricted": False,
            "pendingRequestCount": "1", "escrowedShares": "20",
            "owedAssets": "200000000", "totalAssets": "1000000000",
            "remainingAssets": "800000000", "reservedAssets": "7000000",
            "uncoveredAssets": "0"}


class WithdrawalSizingTests(unittest.TestCase):
    def test_total_target_uses_equity_less_gross_pending(self):
        target = desired_position(snapshot(), Decimal(1000), Decimal(100), Decimal(3), 100)
        self.assertEqual(target, Decimal(24))
        # An existing 30-unit position needs reconciliation/reduction, not a 24-unit buy.
        self.assertEqual(target - Decimal(30), Decimal(-6))

    def test_unknown_is_not_zero_or_a_flattening_instruction(self):
        self.assertIsNone(desired_position(snapshot(), Decimal(1000), Decimal(100), Decimal(3), 120))
        self.assertIsNone(desired_position({}, Decimal(1000), Decimal(100), Decimal(3), 100))

    def test_deduction_floors_negative_capital_and_validates_inputs(self):
        self.assertEqual(desired_position(snapshot(), Decimal(100), Decimal(100), Decimal(3), 100), 0)
        for price in (Decimal(0), Decimal('NaN'), Decimal('Infinity')):
            with self.assertRaises(ValueError):
                desired_position(snapshot(), Decimal(1000), price, Decimal(3), 100)

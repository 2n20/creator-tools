"""Real adapter composition with slow public reads and a fictional SDK writer."""
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hyperliquid.utils.types import Cloid
from executor import execution_state, plan_child
from risk import Stop
from runtime_adapter import RuntimeAdapter
from runtime_io import Admission
from test_acceptance_path import FictionalVenue
from test_runtime import config, intent


class SubmissionTimingTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.delay = 20000
        self.changed = None
        self.venue = FictionalVenue(lambda: self.now, lambda: {})
        self.venue.info.attach_mock(Mock(return_value={"universe": [], "tokens": []}), "spot_meta")
        self.access = SimpleNamespace(scope={"tradingAccount": "disposable"},
                                      verify=Mock(side_effect=self.authority), signer=Mock(return_value=object()))
        self.access.pending_withdrawals = lambda: {"evidence": "verified-public-reads", "blockTimestamp": self.now // 1000,
            "pendingRequestCount": "0", "escrowedShares": "0", "totalAssets": "10000000000", "remainingAssets": "10000000000",
            "restricted": False, "operationInProgress": False}
        self.adapter = RuntimeAdapter(self.access, self.venue.info)
        self.target = {**intent(), "arrivalPrice": "110"}
        self.state = execution_state(self.now)
        self.admission = Admission(lambda: self.now, 301000, 361000)
        self.admission.generation = self.target["id"]
        self.admission.feed_ready.set()
        self.sdk = Mock()
        self.sdk.order.side_effect = self.venue.exchange.order
        self.factory = Mock(return_value=self.sdk)
        previous_logging = logging.root.manager.disable
        self.addCleanup(lambda: logging.disable(previous_logging))
        for patcher in (
            patch("adapter.sdk_types", return_value=(Mock(), self.factory, Cloid)),
            patch("runtime_adapter.sdk_types", return_value=(Mock(), self.factory, Cloid)),
            patch("adapter.time.time", side_effect=lambda: self.now / 1000),
            patch("socket.socket.connect", side_effect=AssertionError("No live network")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        observed = self.adapter.observe("BTC", self.state)
        self.plan = plan_child(self.target, self.state, observed["snapshot"], config()["execution"], self.now)
        self.child = {"cloid": "0x" + "1" * 32, "plan": self.plan}

    def authority(self):
        self.now += self.delay
        if self.changed:
            self.changed()

    def submit(self):
        return self.adapter.submit_child(self.child, self.state, self.target, self.admission)

    def test_slow_public_reads_fit_retained_deadline_without_duplicate_authority(self):
        result = self.submit()
        self.assertEqual(result["classification"], "accepted_unverified")
        self.assertEqual(self.now, 21000)
        self.assertEqual(self.access.verify.call_count, 1)
        self.access.signer.assert_called_once()
        self.sdk.order.assert_called_once()
        self.sdk.set_expires_after.assert_called_once_with(61000)

    def test_slow_final_check_still_expires_before_signer(self):
        self.delay = 61000
        self.assertEqual(self.submit(), {"classification": "not_submitted", "reason": "CHASE_EXPIRED"})
        self.access.signer.assert_not_called()
        self.sdk.order.assert_not_called()

    def test_revocation_during_final_authority_prevents_signing(self):
        self.access.verify.side_effect = Stop("ACCESS_UNVERIFIED", "Disposable revocation")
        self.assertEqual(self.submit()["reason"], "ACCESS_UNVERIFIED")
        self.access.signer.assert_not_called()
        self.sdk.order.assert_not_called()

    def test_cli_read_failure_before_order_is_definitely_not_submitted(self):
        from twon20.errors import SetupError
        for failure in [SetupError('RPC_UNAVAILABLE', 'private-provider-marker'), RuntimeError('private-provider-marker')]:
            with self.subTest(error=type(failure).__name__):
                self.access.verify.side_effect = failure
                result = self.submit()
                self.assertEqual(result, {'classification': 'not_submitted', 'reason': 'ORDER_PREPARATION_UNAVAILABLE'})
                self.access.signer.assert_not_called()
                self.sdk.order.assert_not_called()

    def test_post_signer_pre_order_read_failure_is_not_submitted(self):
        self.adapter._withdrawal_check = Mock(side_effect=[None, RuntimeError('private-provider-marker')])
        self.assertEqual(self.submit(), {'classification': 'not_submitted', 'reason': 'ORDER_PREPARATION_UNAVAILABLE'})
        self.access.signer.assert_called_once()
        self.sdk.order.assert_not_called()

    def test_order_transport_failure_remains_ambiguous(self):
        self.sdk.order.side_effect = TimeoutError('private-provider-marker')
        self.assertEqual(self.submit(), {'classification': 'ambiguous'})
        self.sdk.order.assert_called_once()

    def test_feed_failure_during_final_check_prevents_signing(self):
        self.changed = self.admission.feed_ready.clear
        self.assertEqual(self.submit()["reason"], "FEED_NOT_READY")
        self.access.signer.assert_not_called()
        self.sdk.order.assert_not_called()

    def test_target_replacement_during_final_check_prevents_signing(self):
        self.changed = lambda: setattr(self.admission, "generation", "new-target")
        self.assertEqual(self.submit()["reason"], "TARGET_SUPERSEDED")
        self.access.signer.assert_not_called()
        self.sdk.order.assert_not_called()

    def test_ioc_crosses_quote_movement_within_retained_bound(self):
        limits = config()['execution']
        limits['iocAfterSeconds'] = 5
        self.now += 6000
        current = self.adapter.observe('BTC', self.state)['snapshot']
        self.plan = plan_child(self.target, self.state, current, limits, self.now)
        self.child['plan'] = self.plan
        self.delay = 0
        def match(market, buy, size, price, order_type, **kwargs):
            if price < 110.3:
                return {'status': 'ok', 'response': {'data': {'statuses': [
                    {'error': 'Order could not immediately match against any resting orders.'}]}}}
            return {'status': 'ok', 'response': {'data': {'statuses': [{'filled': {'oid': 1}}]}}}
        self.sdk.order.side_effect = match
        self.assertEqual(self.submit()['classification'], 'accepted_unverified')
        self.assertLessEqual(float(self.plan['price']), 110.55)

    def test_known_exchange_rejection_has_safe_reason_without_provider_text(self):
        self.sdk.order.return_value = None
        self.sdk.order.side_effect = lambda *args, **kwargs: {
            'status': 'ok', 'response': {'data': {'statuses': [
                {'error': 'Order could not immediately match against any resting orders. asset=0'}]}}}
        result = self.submit()
        self.assertEqual(result, {'classification': 'rejected', 'code': 'ORDER_REJECTED', 'reason': 'IOC_NO_MATCH'})
        self.assertNotIn('asset', str(result))

    def test_payload_rejection_with_string_response_is_sanitized(self):
        self.sdk.order.side_effect = lambda *args, **kwargs: {'status': 'err', 'response': 'provider detail'}
        self.assertEqual(self.submit(), {'classification': 'rejected', 'code': 'EXCHANGE_REJECTED'})

    def test_default_exchange_still_requires_fresh_authority(self):
        self.access.verify.side_effect = Stop("ACCESS_UNVERIFIED", "Disposable revocation")
        with self.assertRaises(Stop):
            self.adapter.exchange()
        self.access.signer.assert_not_called()
        self.factory.assert_not_called()

    def test_pending_demand_after_signer_load_blocks_final_order_call(self):
        ready = self.access.pending_withdrawals
        reads = []
        def policy():
            value = ready()
            reads.append(value)
            if len(reads) == 2:
                value["pendingRequestCount"] = "1"
            return value
        self.access.pending_withdrawals = policy
        self.assertEqual(self.submit(), {"classification": "not_submitted", "reason": "WITHDRAWAL_HOLD"})
        self.access.signer.assert_called_once()
        self.sdk.order.assert_not_called()

    def test_remaining_capital_shrink_at_final_order_boundary_blocks_old_target(self):
        ready = self.access.pending_withdrawals
        reads = []
        def policy():
            value = ready()
            reads.append(value)
            if len(reads) == 2:
                value["remainingAssets"] = "1000000"
            return value
        self.access.pending_withdrawals = policy
        self.assertEqual(self.submit(), {"classification": "not_submitted", "reason": "CAPITAL_CHANGED"})
        self.sdk.order.assert_not_called()

"""Read-only fresh capital and independent canonical demand, using public fixtures."""
import time
import unittest
from unittest.mock import patch

from eth_abi import decode, encode
from eth_utils import keccak

from support import CORE, VAULT, FakeRpc
from twon20.discovery import trust_anchor
from twon20.errors import SetupError
from twon20.valuation import ENDPOINT, REPORT_TYPES, fetch_valuation
from twon20.withdrawals import poll_pending_withdrawals, withdrawal_policy


class FreshValuationTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRpc()
        self.now = self.rpc.now
        anchor = trust_anchor('mainnet')
        self.scope = {'network': 'mainnet', 'vault': VAULT, 'tradingAccount': CORE,
                      'approvalContract': anchor['contracts']['LocalVenue'].lower()}
        self.values = [VAULT, 1, 2, self.now, True, 0, 22_540_000, 0, 0, 0,
                       1, '0x' + 'd' * 40, self.now + 3600, 0, 0, 0, False, b'a' * 32]
        self.count, self.shares = 0, 0
        words, call = self.rpc.words, self.rpc.call
        self.simulations = []

        def read(target, name, arguments, block, count):
            if name == 'books(address)':
                value = words(target, name, arguments, block, count)
                value[4] = self.now - 600  # The stored valuation is expired.
                return value
            amounts = {'pendingRequestCount()': self.count, 'balanceOf(address)': self.shares,
                       'reservedAssets()': 100_000, 'totalSupply()': 10,
                       'totalAssets()': 1, 'uncoveredAssets()': 1}
            if target == VAULT and name in amounts:
                return [amounts[name]]
            return words(target, name, arguments, block, count)

        def simulate(method, params):
            if method != 'eth_call':
                return call(method, params)
            tx, block = params
            self.assertEqual(set(tx), {'to', 'data'})
            self.assertEqual(tx['to'], VAULT)
            self.assertEqual(block, {'blockHash': self.rpc.hash, 'requireCanonical': True})
            raw = bytes.fromhex(tx['data'][2:])
            self.assertEqual(raw[:4], keccak(text='withReport(bytes,bytes,bytes,uint256,uint256)')[:4])
            report, signature, action, low, high = decode(['bytes', 'bytes', 'bytes', 'uint256', 'uint256'], raw[4:])
            self.assertEqual(report, encode(REPORT_TYPES, self.values))
            self.assertEqual((low, high), (0, 2 ** 256 - 1))
            self.simulations.append(action)
            if signature != b'\x11' * 65:
                raise SetupError('RPC_UNAVAILABLE', 'Rejected signature fixture')
            self.assertIn(action, [keccak(text=name)[:4] for name in ('totalAssets()', 'uncoveredAssets()')])
            amount = 23_000_000 if action == keccak(text='totalAssets()')[:4] else 0
            return '0x' + encode(['bytes'], [encode(['uint256'], [amount])]).hex()
        self.rpc.words, self.rpc.call = read, simulate
        self.clock = patch('time.time', return_value=self.now)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.transport = patch('twon20.valuation.request_json', side_effect=lambda *a, **k: self.envelope())
        self.transport.start(); self.addCleanup(self.transport.stop)

    def envelope(self):
        return {'status': 'ready', 'generation': trust_anchor('mainnet')['generation'], 'vault': VAULT,
                'observedAt': self.values[3], 'report': '0x' + encode(REPORT_TYPES, self.values).hex(),
                'signature': '0x' + '11' * 65}

    def test_idle_starter_uses_fresh_simulated_whole_vault_capital(self):
        result = poll_pending_withdrawals(self.scope, self.rpc)
        self.assertEqual(result['totalAssets'], '23000000')
        self.assertEqual(result['remainingAssets'], '23000000')
        self.assertEqual(len(self.simulations), 2)
        self.assertTrue(withdrawal_policy(result).ready_to_increase)
        self.assertEqual(str(withdrawal_policy(result).capital.remaining_usdc), '23')
        self.assertEqual(set(method for method, *_ in self.rpc.calls if method.startswith('eth_')), {'eth_getBlockByNumber'})

    def test_positive_demand_still_holds_and_reserves_are_not_subtracted_twice(self):
        self.count, self.shares = 1, 3
        result = poll_pending_withdrawals(self.scope, self.rpc)
        self.assertEqual(result['owedAssets'], '6900000')
        self.assertEqual(result['remainingAssets'], '16100000')
        self.assertFalse(withdrawal_policy(result).ready_to_increase)
        self.assertEqual(withdrawal_policy(result).state, 'yielding')

    def test_invalid_signature_keeps_demand_visible_without_pricing(self):
        self.count, self.shares = 1, 3
        response = self.envelope() | {'signature': '0x' + '22' * 65}
        with patch('twon20.valuation.request_json', return_value=response):
            result = poll_pending_withdrawals(self.scope, self.rpc)
        self.assertEqual(result['pendingRequestCount'], '1')
        self.assertIsNone(result['totalAssets'])
        self.assertFalse(withdrawal_policy(result).ready_to_increase)

    def test_report_expiry_reorg_and_operation_keep_increases_stopped(self):
        self.values[3] = self.now - 44
        result = poll_pending_withdrawals(self.scope, self.rpc)
        self.assertTrue(withdrawal_policy(result).ready_to_increase)
        self.assertFalse(withdrawal_policy(result, self.now + 1).ready_to_increase)
        self.rpc.reorg = True
        self.assertFalse(withdrawal_policy(poll_pending_withdrawals(self.scope, self.rpc)).ready_to_increase)
        self.rpc.reorg = False; self.rpc.phase = 2
        result = poll_pending_withdrawals(self.scope, self.rpc)
        self.assertTrue(result['operationInProgress'])
        self.assertIsNone(result['totalAssets'])

    def test_strict_envelope_scope_and_unknown_evidence(self):
        good = self.envelope()
        for changes in ({'generation': 'other'}, {'vault': CORE}, {'observedAt': True},
                        {'observedAt': self.now - 1}, {'report': '0x00'}, {'status': 'blocked'}):
            with self.subTest(changes=changes), patch('twon20.valuation.request_json', return_value=good | changes):
                with self.assertRaises((ValueError, SetupError)):
                    fetch_valuation(self.scope)
        for index, value in ((3, self.now - 45), (3, self.now + 1), (4, False), (13, 1), (17, bytes(32))):
            prior = self.values[index]; self.values[index] = value
            with self.assertRaises(ValueError): fetch_valuation(self.scope)
            self.values[index] = prior
        with patch('twon20.valuation.request_json', side_effect=SetupError('UNAVAILABLE', 'private-marker')):
            result = poll_pending_withdrawals(self.scope, self.rpc)
        self.assertIsNone(result['totalAssets'])
        self.assertEqual(result['pendingRequestCount'], '0')

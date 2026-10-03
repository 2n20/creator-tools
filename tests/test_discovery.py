import unittest

from twon20.discovery import ZERO, discover, vault_link
from twon20.errors import SetupError
from twon20.rpc import Rpc, integer

from support import CORE, LINK, VAULT, FakeRpc


class DiscoveryTests(unittest.TestCase):
    def discover(self, rpc):
        return discover(LINK, rpc, rpc.now, rpc.anchor)

    def test_verified_discovery_has_separate_public_identities(self):
        rpc = FakeRpc()
        found = self.discover(rpc)
        self.assertEqual(found.vault, VAULT)
        self.assertEqual(found.trading_account, CORE)
        self.assertNotEqual(found.trading_account, found.approval_contract)
        self.assertEqual(found.nonce, 0)
        self.assertFalse(any(call[0] in ("eth_sendTransaction", "exchange", "info") for call in rpc.calls))
        for call in rpc.calls:
            if len(call) == 3:
                self.assertTrue(call[2]["requireCanonical"])

    def test_preparing_metadata_does_not_require_funded_core(self):
        rpc = FakeRpc()
        rpc.core = ZERO
        found = self.discover(rpc)
        self.assertEqual(found.trading_account, ZERO)

    def test_reject_arbitrary_origins_paths_query_values_and_networks(self):
        bad = [LINK.replace("2n20.org", "attacker.invalid"), LINK.replace("https", "http"), LINK + "&nonce=1", LINK + "&rpc=https://example.org", LINK + "&venue=0x123", LINK + "&network=mainnet", LINK.replace("mainnet", "testnet"), LINK.replace("mainnet", "local"), LINK.replace(VAULT, ZERO), LINK.replace("2n20.org", "2n20.org:443"), LINK + "#mainnet", LINK.replace("2n20.org", "user@2n20.org"), LINK.replace("/vaults/", "/vault/")]
        for value in bad:
            with self.subTest(link=value), self.assertRaises(SetupError):
                vault_link(value)

    def test_network_default_is_mainnet_and_case_is_normalized(self):
        self.assertEqual(vault_link(f"https://2n20.org/vaults/{VAULT.upper().replace('0X', '0x')}")[0], VAULT)
        self.assertEqual(vault_link(f"https://2n20.org/vaults/{VAULT}")[1], "mainnet")

    def test_wrong_chain_factory_membership_and_capabilities(self):
        for name, value, code in [("chain", 998, "WRONG_CHAIN"), ("registered", 0, "UNREGISTERED_VAULT"), ("version", 1, "UNSUPPORTED_CAPABILITY"), ("bad_factory", True, "INVALID_ADDRESS"), ("bad_core", True, "ACCOUNT_BINDING_MISMATCH"), ("bound_venue", "0x" + "e" * 40, "FACTORY_BINDING_MISMATCH")]:
            rpc = FakeRpc()
            setattr(rpc, name, value)
            with self.subTest(name=name), self.assertRaises(SetupError) as caught:
                self.discover(rpc)
            self.assertEqual(caught.exception.code, code)

    def test_code_hash_and_clone_implementation_are_enforced(self):
        for address, code in [(FakeRpc().anchor["contracts"]["LocalVenue"].lower(), "CONTRACT_CODE_MISMATCH"), (VAULT, "UNSUPPORTED_VAULT")]:
            rpc = FakeRpc()
            rpc.codes[address] = "0x6000"
            with self.assertRaises(SetupError) as caught:
                self.discover(rpc)
            self.assertEqual(caught.exception.code, code)

    def test_missing_receipt_rpc_failure_and_reorg_fail_closed(self):
        for method in ("eth_getTransactionReceipt", "eth_getCode", "tradingKeyVersion()", "coreAccountOf(address)", "book(address)"):
            rpc = FakeRpc()
            rpc.fail = method
            with self.subTest(method=method), self.assertRaises(SetupError):
                self.discover(rpc)
        rpc = FakeRpc()
        rpc.reorg = True
        with self.assertRaises(SetupError) as caught:
            self.discover(rpc)
        self.assertEqual(caught.exception.code, "CHAIN_CHANGED")

    def test_stale_clock_and_nonce_overflow_fail_closed(self):
        rpc = FakeRpc()
        with self.assertRaises(SetupError):
            discover(LINK, rpc, rpc.now + 301, rpc.anchor)
        rpc.key[2] = 2 ** 64 - 1
        with self.assertRaises(SetupError) as caught:
            self.discover(rpc)
        self.assertEqual(caught.exception.code, "NONCE_EXHAUSTED")

    def test_transport_is_read_only_and_invalid_quantities_rejected(self):
        with self.assertRaises(SetupError):
            Rpc("mainnet").call("eth_sendRawTransaction", [])
        for value in [None, 999, "-0x1", "0x", "invalid", "0x+1", "0x1_0", "0x 1", "0x1 "]:
            with self.assertRaises(SetupError):
                integer(value)

    def test_malformed_and_mismatched_deployment_receipts_are_rejected(self):
        for field, value in [("transactionHash", 1), ("blockHash", None), ("status", "0x0"), ("contractAddress", VAULT)]:
            rpc = FakeRpc()
            original = rpc.call
            def changed(method, params):
                result = original(method, params)
                return result | {field: value} if method == "eth_getTransactionReceipt" else result
            rpc.call = changed
            with self.subTest(field=field), self.assertRaises(SetupError):
                self.discover(rpc)

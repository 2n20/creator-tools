import json
import re
import unittest
from pathlib import Path

from twon20.discovery import ZERO, discover, parse_book, parse_key, parse_release, parse_strikes, vault_link
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
        rpc.core, rpc.priced, rpc.report_nonce = ZERO, 0, 0
        found = self.discover(rpc)
        self.assertEqual(found.trading_account, ZERO)
        self.assertEqual((found.book.priced, found.book.unpriced, found.book.report_nonce), (False, 0, 0), "a vault before its first report reads unpriced with no reason")

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
        for method in ("eth_getTransactionReceipt", "eth_getCode", "tradingKeyVersion()", "coreAccountOf(address)", "books(address)"):
            rpc = FakeRpc()
            rpc.fail = method
            with self.subTest(method=method), self.assertRaises(SetupError):
                self.discover(rpc)
        rpc = FakeRpc()
        rpc.reorg = True
        with self.assertRaises(SetupError) as caught:
            self.discover(rpc)
        self.assertEqual(caught.exception.code, "CHAIN_CHANGED")

    def test_trading_key_version_two_fails_closed(self):
        rpc = FakeRpc()
        rpc.version = 2
        with self.assertRaises(SetupError) as caught:
            self.discover(rpc)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(caught.exception.message, "This approval contract's trading-key version is not 4. Setup stops. No key was created.")

    def test_trading_key_version_three_fails_closed(self):
        rpc = FakeRpc()
        rpc.version = 3
        with self.assertRaises(SetupError) as caught:
            self.discover(rpc)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(caught.exception.message, "This approval contract's trading-key version is not 4. Setup stops. No key was created.")

    def test_unexpected_release_rules_fail_closed(self):
        for name, value in (("release_notice", 60), ("release_round", 300)):
            rpc = FakeRpc()
            setattr(rpc, name, value)
            with self.subTest(name=name), self.assertRaises(SetupError) as caught:
                self.discover(rpc)
            self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
            self.assertEqual(caught.exception.message, "This approval contract's withdrawal release rules are not the reviewed ones. Setup stops.")

    def test_release_words_match_the_reviewed_shape(self):
        for words in ((0,) * 9, (0,) * 11):
            with self.subTest(words=len(words)), self.assertRaises(SetupError) as caught:
                parse_release(words)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
            self.assertEqual(caught.exception.message, "The public trading-account state is inconsistent.")
        for words in ((0, 0, 0, 0, 0, 2, 0, 0, 0, 0), (0, 0, 0, 0, 2, 0, 0, 0, 0, 0), (-1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
                      (0, 0, 0, 0, 0, 0, 0, 2 ** 64, 0, 0), (0, 0, 0, 0, 0, 0, 0, 0, 2 ** 64, 0), (0, 0, 0, 0, 0, 0, 0, 0, 0, 2 ** 64)):
            with self.subTest(words=words), self.assertRaises(SetupError) as caught:
                parse_release(words)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
            self.assertEqual(caught.exception.message, "The public trading-account state is inconsistent.")
        parsed = parse_release((11, 22, 33, 44, 1, 0, 55, 77, 66, 88))
        self.assertEqual((parsed.round_at, parsed.need, parsed.released, parsed.captured, parsed.opened, parsed.executed,
                          parsed.pending, parsed.pending_margin, parsed.cancelled, parsed.notice_at),
                         (11, 22, 33, 44, True, False, 55, 77, 66, 88))

    def book(self, priced=1, unpriced=0, orders=4, exposure=9, report_nonce=2):
        return (int(CORE, 16), 0, 1, report_nonce, 3, 4, 0, 0, priced, unpriced, 5, 6, 7, exposure, orders, 8)

    def test_book_word_count_and_pricing_fail_closed(self):
        for words in (self.book()[:15], self.book() + (9,)):
            with self.subTest(words=len(words)), self.assertRaises(SetupError) as caught:
                parse_book(words, CORE)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
        for priced, unpriced in ((1, 1), (0, 0), (0, 512), (1, 512), (0, 65535)):
            with self.subTest(priced=priced, unpriced=unpriced), self.assertRaises(SetupError) as caught:
                parse_book(self.book(priced, unpriced), CORE)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
            self.assertEqual(caught.exception.message, "The public trading-account state is inconsistent.")
        priced = parse_book(self.book(), CORE)
        self.assertTrue(priced.priced)
        self.assertEqual((priced.unpriced, priced.core_assets, priced.pending_assets, priced.evm_baseline, priced.exposure, priced.orders, priced.report_hash),
                         (0, 5, 6, 7, 9, 4, 8))
        unpriced = parse_book(self.book(0, 511), CORE)
        self.assertFalse(unpriced.priced)
        self.assertEqual(unpriced.unpriced, 511)
        first = parse_book(self.book(0, 0, report_nonce=0), CORE)
        self.assertEqual((first.priced, first.unpriced, first.report_nonce), (False, 0, 0))
        with self.assertRaises(SetupError):
            parse_book(self.book(1, 1, report_nonce=0), CORE)

    def test_parsers_follow_the_shared_venue_field_order(self):
        names = json.loads((Path(__file__).parent / "fixtures" / "interoperability.public.json").read_text())["venueReads"]
        a, b, c, d = ("0x" + digit * 40 for digit in "1234")
        core = int(CORE, 16)
        cases = {
            "books": [
                (parse_book((core, 2, 3, 4, 5, 6, 1, 0, 1, 0, 10, 11, 12, 13, 14, 15), CORE),
                 (CORE, 2, 3, 4, 5, 6, True, False, True, 0, 10, 11, 12, 13, 14, 15)),
                (parse_book((core, 1, 3, 4, 5, 6, 0, 1, 1, 0, 10, 11, 12, 2 ** 256 - 13, 14, 15), CORE),
                 (CORE, 1, 3, 4, 5, 6, False, True, True, 0, 10, 11, 12, -13, 14, 15)),
                (parse_book((core, 3, 3, 4, 5, 6, 1, 1, 0, 9, 10, 11, 12, 13, 14, 15), CORE),
                 (CORE, 3, 3, 4, 5, 6, True, True, False, 9, 10, 11, 12, 13, 14, 15)),
            ],
            "keys": [(parse_key((int(a, 16), 1, 5, int(b, 16), 6, int(c, 16), int(d, 16))), (a, True, 5, b, 6, c, d))],
            "releases": [(parse_release((11, 22, 33, 44, 1, 0, 55, 77, 66, 88)), (11, 22, 33, 44, True, False, 55, 77, 66, 88))],
            "strikes": [(parse_strikes((2, 99)), (2, 99))],
        }
        self.assertEqual(set(cases), set(names))
        for read, vectors in cases.items():
            for parsed, expected in vectors:
                with self.subTest(read=read):
                    self.assertEqual(tuple(getattr(parsed, re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()) for name in names[read]), expected)

    def test_strike_words_fail_closed(self):
        for words in ((0,), (0, 0, 0), (256, 0), (-1, 0), (1, 2 ** 64)):
            with self.subTest(words=words), self.assertRaises(SetupError) as caught:
                parse_strikes(words)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
        parsed = parse_strikes((2, 99))
        self.assertEqual((parsed.misses, parsed.last_miss_at), (2, 99))

    def test_short_reads_and_inconsistent_pricing_fail_discovery(self):
        for name in ("short_book", "short_release", "short_strikes"):
            rpc = FakeRpc()
            setattr(rpc, name, True)
            with self.subTest(name=name), self.assertRaises(SetupError) as caught:
                self.discover(rpc)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
        for priced, unpriced in ((1, 1), (0, 0), (0, 512)):
            rpc = FakeRpc()
            rpc.priced, rpc.unpriced = priced, unpriced
            with self.subTest(priced=priced, unpriced=unpriced), self.assertRaises(SetupError) as caught:
                self.discover(rpc)
            self.assertEqual(caught.exception.code, "ACCOUNT_BINDING_MISMATCH")
        rpc = FakeRpc()
        rpc.priced, rpc.unpriced = 0, 511
        found = self.discover(rpc)
        self.assertFalse(found.book.priced)
        self.assertEqual(found.book.unpriced, 511)

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

import json
import unittest

from eth_account import Account
from eth_account.messages import encode_typed_data

from twon20.consent import FIELDS, prepare, typed_data, validate
from twon20.discovery import discover
from twon20.errors import SetupError

from support import LINK, FakeRpc


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.rpc = FakeRpc()
        self.found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        self.wallet = Account.create()
        self.consent = prepare(self.found, self.wallet, self.rpc, self.rpc.now)

    def test_exact_schema_solidity_digest_and_signature_interoperate(self):
        self.assertEqual(set(self.consent), FIELDS)
        self.assertEqual(self.consent["nonce"], "1")
        self.assertEqual(self.consent["deadline"], str(self.rpc.now + 3600))
        recovered = Account.recover_message(encode_typed_data(full_message=typed_data(self.found, self.wallet.address, 1, self.rpc.now + 3600)), signature=self.consent["signature"])
        self.assertEqual(recovered, self.wallet.address)
        checked = validate(json.dumps(self.consent), self.found, self.rpc, self.rpc.now)
        self.assertEqual(checked, self.consent)
        self.assertNotIn(self.wallet.key.hex(), json.dumps(checked))

    def test_stale_nonce_expiry_wrong_network_and_binding(self):
        for field, value, code in [("nonce", "2", "STALE_NONCE"), ("deadline", str(self.rpc.now), "EXPIRED_CONSENT"), ("deadline", str(self.rpc.now + 86401), "EXPIRED_CONSENT"), ("chainId", 998, "INVALID_CONSENT"), ("vault", "0x" + "e" * 40, "CONSENT_BINDING_MISMATCH"), ("venue", "0x" + "e" * 40, "CONSENT_BINDING_MISMATCH"), ("signature", "0x" + "00" * 65, "INVALID_SIGNATURE")]:
            value = dict(self.consent) | {field: value}
            with self.subTest(field=field), self.assertRaises(SetupError) as caught:
                validate(json.dumps(value), self.found, self.rpc, self.rpc.now)
            self.assertEqual(caught.exception.code, code)

    def test_signature_from_different_key_is_rejected(self):
        altered = self.consent | {"key": Account.create().address}
        with self.assertRaises(SetupError) as caught:
            validate(json.dumps(altered), self.found, self.rpc, self.rpc.now)
        self.assertEqual(caught.exception.code, "INVALID_SIGNATURE")

    def test_unsupported_extra_fields_and_key_material_rejected(self):
        for value in [self.consent | {"privateKey": "redacted-disposable-placeholder"}, {"schema": "wrong"}, [self.consent], "redacted-disposable-placeholder"]:
            with self.assertRaises(SetupError):
                validate(json.dumps(value), self.found, self.rpc, self.rpc.now)

    def test_expiry_and_changed_nonce_instructions_reuse_existing_key(self):
        changed = self.consent | {"nonce": "3"}
        with self.assertRaises(SetupError) as caught:
            validate(json.dumps(changed), self.found, self.rpc, self.rpc.now)
        self.assertIn("2n20 renew --directory", caught.exception.message)

    def test_digest_mismatch_and_used_key_are_rejected(self):
        self.rpc.bad_digest = True
        with self.assertRaises(SetupError) as caught:
            prepare(self.found, self.wallet, self.rpc, self.rpc.now)
        self.assertEqual(caught.exception.code, "CONSENT_FORMAT_MISMATCH")
        self.rpc.bad_digest = False
        self.rpc.used = True
        with self.assertRaises(SetupError) as caught:
            prepare(self.found, self.wallet, self.rpc, self.rpc.now)
        self.assertEqual(caught.exception.code, "KEY_ALREADY_USED")

    def test_state_change_before_output_is_rejected(self):
        original = self.rpc.words
        def changed(target, signature, arguments, block, count):
            value = original(target, signature, arguments, block, count)
            return [value[0], value[1], 1] if signature == "tradingKeys(address)" else value
        self.rpc.words = changed
        with self.assertRaises(SetupError) as caught:
            prepare(self.found, self.wallet, self.rpc, self.rpc.now)
        self.assertEqual(caught.exception.code, "STATE_CHANGED")

    def test_duplicate_fields_and_malleable_signature_are_rejected(self):
        text = json.dumps(self.consent)[:-1] + ',"nonce":"1"}'
        with self.assertRaises(SetupError):
            validate(text, self.found, self.rpc, self.rpc.now)
        raw = bytes.fromhex(self.consent["signature"][2:])
        order = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        high_s = order - int.from_bytes(raw[32:64], "big")
        signature = raw[:32] + high_s.to_bytes(32, "big") + bytes([55 - raw[64]])
        with self.assertRaises(SetupError) as caught:
            validate(json.dumps(self.consent | {"signature": "0x" + signature.hex()}), self.found, self.rpc, self.rpc.now)
        self.assertEqual(caught.exception.code, "INVALID_SIGNATURE")

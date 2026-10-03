"""Exact TradingKeyConsent EIP-712 format shared with the app and contract."""

import re
import time

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from eth_keys.exceptions import BadSignature

from .discovery import Discovery, UINT64_MAX, ZERO, address, code_bytes, verify_current
from .errors import SetupError
from .rpc import Rpc, parse_json

SCHEMA = "2n20-trading-key-consent-v1"
FIELDS = frozenset({"schema", "chainId", "venue", "vault", "key", "nonce", "deadline", "signature"})


def typed_data(found: Discovery, key: str, nonce: int, deadline: int) -> dict:
    return {"domain": {"name": "2n20 SDK venue", "version": "1", "chainId": found.anchor["chainId"], "verifyingContract": found.approval_contract},
            "primaryType": "TradingKeyConsent",
            "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                      "TradingKeyConsent": [{"name": "vault", "type": "address"}, {"name": "key", "type": "address"}, {"name": "nonce", "type": "uint64"}, {"name": "deadline", "type": "uint64"}]},
            "message": {"vault": found.vault, "key": key, "nonce": nonce, "deadline": deadline}}


def digest(found: Discovery, key: str, nonce: int, deadline: int) -> bytes:
    message = encode_typed_data(full_message=typed_data(found, key, nonce, deadline))
    return keccak(b"\x19" + message.version + message.header + message.body)


def verify_digest(found: Discovery, key: str, nonce: int, deadline: int, rpc: Rpc):
    returned = rpc.words(found.approval_contract, "consentDigest(address,address,uint64,uint64)",
                         [found.vault, key, nonce, deadline], found.fixed, 1)[0]
    if returned != int.from_bytes(digest(found, key, nonce, deadline), "big"):
        raise SetupError("CONSENT_FORMAT_MISMATCH", "The approval contract does not recognize this release's consent format.")


def prepare(found: Discovery, wallet, rpc: Rpc, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    key = address(wallet.address)
    if key in {ZERO, found.vault, found.owner, found.trading_account, found.requested_key}:
        raise SetupError("KEY_NOT_FRESH", "The trading key is not a fresh independent account. Its private file remains unchanged.")
    if code_bytes(rpc.call("eth_getCode", [key, found.fixed])) or rpc.words(found.approval_contract, "usedTradingKeys(address)", [key], found.fixed, 1) != [0]:
        raise SetupError("KEY_ALREADY_USED", "This trading key has already been used. Keep it private and run setup in a new directory for a fresh key.")
    nonce, deadline = found.nonce + 1, now + 3600
    if deadline <= found.timestamp or deadline > found.timestamp + 86400:
        raise SetupError("CLOCK_MISMATCH", "Check the machine clock before preparing consent.")
    verify_digest(found, key, nonce, deadline, rpc)
    signature = wallet.sign_message(encode_typed_data(full_message=typed_data(found, key, nonce, deadline))).signature.hex()
    value = {"schema": SCHEMA, "chainId": found.anchor["chainId"], "venue": found.approval_contract,
             "vault": found.vault, "key": wallet.address, "nonce": str(nonce), "deadline": str(deadline),
             "signature": "0x" + signature.removeprefix("0x")}
    current = verify_current(found, rpc)
    if code_bytes(rpc.call("eth_getCode", [key, current])) or rpc.words(found.approval_contract, "usedTradingKeys(address)", [key], current, 1) != [0]:
        raise SetupError("KEY_ALREADY_USED", "The trading key was used during verification. Keep the existing key private and create a fresh setup in a new directory.")
    return value


def validate(text: str, found: Discovery, rpc: Rpc, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    if len(text.encode("utf-8")) > 2000:
        raise SetupError("INVALID_CONSENT", "Use only the small public consent JSON file, never strategy.key.")
    try:
        value = parse_json(text)
    except ValueError:
        raise SetupError("INVALID_CONSENT", "Select the public consent JSON file, never strategy.key.") from None
    if not isinstance(value, dict) or set(value) != FIELDS or value.get("schema") != SCHEMA or type(value.get("chainId")) is not int or value["chainId"] != found.anchor["chainId"]:
        raise SetupError("INVALID_CONSENT", "This public consent file does not match the supported schema and network.")
    if address(value["vault"]) != found.vault or address(value["venue"]) != found.approval_contract:
        raise SetupError("CONSENT_BINDING_MISMATCH", "This public consent is for a different vault or approval contract.")
    key = address(value["key"])
    if key in {found.vault, found.owner, found.trading_account, found.requested_key}:
        raise SetupError("KEY_NOT_FRESH", "Consent must use a fresh independent trading key.")
    if not isinstance(value["nonce"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value["nonce"]) or int(value["nonce"]) != found.nonce + 1:
        raise SetupError("STALE_NONCE", "The approval nonce changed. Run 2n20 renew --directory <your-setup-directory> on the strategy machine and import the new public file.")
    if not isinstance(value["deadline"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value["deadline"]) or int(value["deadline"]) > UINT64_MAX:
        raise SetupError("INVALID_EXPIRY", "This consent has an invalid expiry. Renew it on the strategy machine.")
    deadline = int(value["deadline"])
    if deadline <= max(now, found.timestamp) or deadline > min(now, found.timestamp) + 86400:
        raise SetupError("EXPIRED_CONSENT", "Consent expired or exceeds 24 hours. Run 2n20 renew --directory <your-setup-directory> on the strategy machine and import the new public file.")
    if not isinstance(value["signature"], str) or not re.fullmatch(r"0x[0-9a-fA-F]{130}", value["signature"]):
        raise SetupError("INVALID_SIGNATURE", "This consent has an invalid public signature.")
    signature_bytes = bytes.fromhex(value["signature"][2:])
    if signature_bytes[64] not in (27, 28) or not 0 < int.from_bytes(signature_bytes[32:64], "big") <= 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0:
        raise SetupError("INVALID_SIGNATURE", "The signature must use the canonical contract ECDSA format.")
    try:
        recovered = Account.recover_message(encode_typed_data(full_message=typed_data(found, key, found.nonce + 1, deadline)), signature=value["signature"])
    except (ValueError, TypeError, OverflowError, BadSignature):
        raise SetupError("INVALID_SIGNATURE", "The consent signature could not be verified.") from None
    if address(recovered) != key:
        raise SetupError("INVALID_SIGNATURE", "The signature does not belong to this trading key.")
    if code_bytes(rpc.call("eth_getCode", [key, found.fixed])) or rpc.words(found.approval_contract, "usedTradingKeys(address)", [key], found.fixed, 1) != [0]:
        raise SetupError("KEY_ALREADY_USED", "This trading key cannot be approved again. Create a fresh setup in a new directory.")
    verify_digest(found, key, found.nonce + 1, deadline, rpc)
    current = verify_current(found, rpc)
    if code_bytes(rpc.call("eth_getCode", [key, current])) or rpc.words(found.approval_contract, "usedTradingKeys(address)", [key], current, 1) != [0]:
        raise SetupError("KEY_ALREADY_USED", "The trading key was used during verification. Keep the existing key private and create a fresh setup in a new directory.")
    return value

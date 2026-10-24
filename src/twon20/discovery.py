"""Discovery uses bundled release trust anchors and canonical public EVM reads.

A vault URL selects only its address and a supported network. It cannot supply
an RPC, approval contract, deployment, nonce or trading account.
"""

import json
import re
import time
from dataclasses import dataclass
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from eth_utils import keccak

from .errors import SetupError
from .rpc import Rpc, integer

ZERO = "0x" + "0" * 40
HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
UINT64_MAX = 2 ** 64 - 1
TRADING_KEY_VERSION = 4
RELEASE_NOTICE_SECONDS = 120
RELEASE_ROUND_SECONDS = 600
RELEASE_WORDS = 10
BOOK_WORDS = 16
KEY_WORDS = 7
STRIKE_WORDS = 2
UNPRICED_LIMIT = 511
MISMATCH = "The public trading-account state is inconsistent."


def address(value, allow_zero=False) -> str:
    if not isinstance(value, str) or not ADDRESS.fullmatch(value) or not allow_zero and value.lower() == ZERO:
        raise SetupError("INVALID_ADDRESS", "Use a valid nonzero public Ethereum address.")
    return value.lower()


def decoded_address(value: int, allow_zero=False) -> str:
    if not 0 <= value < 2 ** 160:
        raise SetupError("INVALID_CONTRACT_READ", "A contract returned an invalid address.")
    return address(f"0x{value:040x}", allow_zero)


def vault_link(value: str) -> tuple[str, str, str]:
    try:
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else {}
    except ValueError:
        raise SetupError("INVALID_VAULT_LINK", "Copy the vault link from https://2n20.org.") from None
    if parsed.scheme != "https" or parsed.netloc != "2n20.org" or parsed.fragment or set(query) - {"network"}:
        raise SetupError("INVALID_VAULT_LINK", "Copy the vault link from https://2n20.org. The link may contain only the network selector.")
    if "network" in query and len(query["network"]) != 1:
        raise SetupError("INVALID_VAULT_LINK", "Use one network selector in the vault link.")
    match = re.fullmatch(r"/vaults/(0x[0-9a-fA-F]{40})/?", parsed.path)
    if not match:
        raise SetupError("INVALID_VAULT_LINK", "Copy the full vault page link from https://2n20.org.")
    network = query.get("network", ["mainnet"])[0]
    if network != "mainnet":
        raise SetupError("UNSUPPORTED_NETWORK", "This release supports verified HyperEVM mainnet deployments only. Do not change networks to bypass this check.")
    vault = address(match[1])
    return vault, network, f"https://2n20.org/vaults/{vault}?network={network}"


def trust_anchor(network: str) -> dict:
    if network != "mainnet":
        raise SetupError("UNSUPPORTED_NETWORK", "No reviewed deployment is bundled for this network.")
    return json.loads(files("twon20").joinpath("deployments.json").read_text(encoding="utf-8"))


def same_hash(value, expected: str) -> bool:
    return isinstance(value, str) and bool(HASH.fullmatch(value)) and value.lower() == expected.lower()


def block_identity(value: dict, now: int | None = None) -> tuple[int, str, int]:
    if not isinstance(value, dict) or not isinstance(value.get("hash"), str) or not HASH.fullmatch(value["hash"]):
        raise SetupError("INVALID_RPC_EVIDENCE", "The RPC did not provide a canonical block identity.")
    number, timestamp = integer(value.get("number")), integer(value.get("timestamp"))
    if now is not None and (timestamp > now + 5 or now - timestamp > 300):
        raise SetupError("STALE_CHAIN", "The public chain snapshot is stale or your clock is incorrect. Check your clock and try again.")
    return number, value["hash"].lower(), timestamp


def code_bytes(value) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise SetupError("INVALID_CONTRACT_READ", "The RPC returned invalid contract code.")
    return bytes.fromhex(value[2:])


def uint64(value) -> int:
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    return value


def flag(value) -> bool:
    if type(value) is not int or value not in (0, 1):
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    return bool(value)


def word256(value) -> int:
    if type(value) is not int or not 0 <= value < 2 ** 256:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    return value


@dataclass(frozen=True)
class Book:
    core: str
    phase: int
    state_nonce: int
    report_nonce: int
    observed_at: int
    restricted_at: int
    restricted: bool
    recovered: bool
    priced: bool
    unpriced: int
    core_assets: int
    pending_assets: int
    evm_baseline: int
    exposure: int
    orders: int
    report_hash: int
    words: tuple


@dataclass(frozen=True)
class Key:
    requested: str
    revocation_pending: bool
    nonce: int
    confirmed: str
    valid_until: int
    nominator: str
    replacement: str
    words: tuple


def parse_book(words, core: str) -> Book:
    if len(words) != BOOK_WORDS:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    parsed = decoded_address(words[0], True)
    phase = words[1]
    if type(phase) is not int or phase not in (0, 1, 2, 3) or parsed != core:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    unpriced = words[9]
    if type(unpriced) is not int or not 0 <= unpriced <= UNPRICED_LIMIT:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    priced = flag(words[8])
    report_nonce = uint64(words[3])
    # Every report names a reason exactly when it leaves the account unpriced. A book no report has reached is unpriced with none.
    if (priced and unpriced) or (not priced and not unpriced and report_nonce):
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    orders = words[14]
    if type(orders) is not int or not 0 <= orders <= 2 ** 32 - 1:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    exposure = word256(words[13])
    return Book(parsed, phase, uint64(words[2]), report_nonce, uint64(words[4]), uint64(words[5]),
                flag(words[6]), flag(words[7]), priced, unpriced, word256(words[10]), word256(words[11]), word256(words[12]),
                exposure - 2 ** 256 if exposure >= 2 ** 255 else exposure, orders, word256(words[15]), tuple(words))


def parse_key(words) -> Key:
    if len(words) != KEY_WORDS:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    return Key(decoded_address(words[0], True), flag(words[1]), uint64(words[2]), decoded_address(words[3], True),
               uint64(words[4]), decoded_address(words[5], True), decoded_address(words[6], True), tuple(words))


@dataclass(frozen=True)
class Release:
    round_at: int
    need: int
    released: int
    captured: int
    opened: bool
    executed: bool
    pending: int
    pending_margin: int
    cancelled: int
    notice_at: int
    words: tuple


def parse_release(words) -> Release:
    if len(words) != RELEASE_WORDS:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    return Release(uint64(words[0]), uint64(words[1]), uint64(words[2]), uint64(words[3]), flag(words[4]), flag(words[5]),
                   uint64(words[6]), uint64(words[7]), uint64(words[8]), uint64(words[9]), tuple(words))


@dataclass(frozen=True)
class Strikes:
    misses: int
    last_miss_at: int
    words: tuple


def parse_strikes(words) -> Strikes:
    if len(words) != STRIKE_WORDS:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    misses = words[0]
    if type(misses) is not int or not 0 <= misses <= 255:
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    return Strikes(misses, uint64(words[1]), tuple(words))


def read_account(rpc: Rpc, venue: str, vault: str, core: str, block: dict):
    rpc.prefetch_words([(venue, signature, []) for signature in ("tradingKeyVersion()", "RELEASE_NOTICE()", "RELEASE_ROUND()")]
        + [(venue, signature, [vault]) for signature in ("books(address)", "keys(address)", "releases(address)", "strikes(address)", "cutsAllowedAt(address)")]
        + [(vault, "uncoveredAssets()", [])], block)
    if rpc.words(venue, "tradingKeyVersion()", [], block, 1) != [TRADING_KEY_VERSION]:
        raise SetupError("UNSUPPORTED_CAPABILITY", "This approval contract's trading-key version is not 4. Setup stops. No key was created.")
    if rpc.words(venue, "RELEASE_NOTICE()", [], block, 1) != [RELEASE_NOTICE_SECONDS] or rpc.words(venue, "RELEASE_ROUND()", [], block, 1) != [RELEASE_ROUND_SECONDS]:
        raise SetupError("UNSUPPORTED_CAPABILITY", "This approval contract's withdrawal release rules are not the reviewed ones. Setup stops.")
    book = parse_book(tuple(rpc.words(venue, "books(address)", [vault], block, BOOK_WORDS)), core)
    key = parse_key(tuple(rpc.words(venue, "keys(address)", [vault], block, KEY_WORDS)))
    release = parse_release(tuple(rpc.words(venue, "releases(address)", [vault], block, RELEASE_WORDS)))
    strikes = parse_strikes(tuple(rpc.words(venue, "strikes(address)", [vault], block, STRIKE_WORDS)))
    cuts_allowed_at = uint64(rpc.words(venue, "cutsAllowedAt(address)", [vault], block, 1)[0])
    uncovered = rpc.words(vault, "uncoveredAssets()", [], block, 1)[0]
    word256(uncovered)
    return book, key, release, strikes, cuts_allowed_at, uncovered


@dataclass(frozen=True)
class Discovery:
    vault: str
    network: str
    link: str
    anchor: dict
    owner: str
    approval_contract: str
    trading_account: str
    requested_key: str
    revocation_pending: bool
    nonce: int
    block_number: int
    block_hash: str
    timestamp: int
    book: Book
    key: Key
    release: Release
    strikes: Strikes
    cuts_allowed_at: int
    lifecycle: int
    pending_requests: int
    uncovered: int

    @property
    def fixed(self) -> dict:
        return {"blockHash": self.block_hash, "requireCanonical": True}

    def public(self) -> dict:
        return {"schema": "2n20-strategy-config-v1", "network": self.network, "chainId": self.anchor["chainId"],
                "vaultLink": self.link, "vault": self.vault, "approvalContract": self.approval_contract,
                "tradingAccount": self.trading_account, "creatorWallet": self.owner,
                "hyperliquidInfoUrl": "https://api.hyperliquid.xyz/info",
                "hyperliquidExchangeUrl": "https://api.hyperliquid.xyz/exchange",
                "requestedTradingKey": self.requested_key, "nextApprovalNonce": str(self.nonce + 1),
                "blockNumber": str(self.block_number), "blockHash": self.block_hash,
                "strategyKeyFile": "strategy.key", "strategySupplied": False}


def discover(link: str, rpc: Rpc | None = None, now: int | None = None, anchor: dict | None = None) -> Discovery:
    vault, network, canonical_link = vault_link(link)
    anchor = anchor if anchor is not None else trust_anchor(network)
    rpc = rpc if rpc is not None else Rpc(network)
    chain, deployment_block, receipt, current = rpc.batch([
        ("eth_chainId", []), ("eth_getBlockByNumber", [hex(int(anchor["deploymentBlock"])), False]),
        ("eth_getTransactionReceipt", [anchor["deploymentHash"]]), ("eth_getBlockByNumber", ["latest", False])])
    chain_id = integer(chain)
    if chain_id != anchor["chainId"] or chain_id != 999:
        raise SetupError("WRONG_CHAIN", "The public RPC is on the wrong chain. No key was created.")
    if not isinstance(deployment_block, dict) or not same_hash(deployment_block.get("hash"), anchor["genesisHash"]) or integer(deployment_block.get("number")) != int(anchor["deploymentBlock"]):
        raise SetupError("UNVERIFIED_DEPLOYMENT", "The deployment block does not match this release's reviewed trust anchor.")
    if not isinstance(receipt, dict) or not same_hash(receipt.get("transactionHash"), anchor["deploymentHash"]) or not same_hash(receipt.get("blockHash"), anchor["genesisHash"]) or integer(receipt.get("status")) != 1 or integer(receipt.get("blockNumber")) != int(anchor["deploymentBlock"]) or address(receipt.get("contractAddress")) != address(anchor["contracts"]["VaultFactory"]):
        raise SetupError("UNVERIFIED_DEPLOYMENT", "The factory deployment receipt does not match the reviewed deployment.")
    number, block_hash, timestamp = block_identity(current, int(time.time()) if now is None else now)
    fixed = {"blockHash": block_hash, "requireCanonical": True}
    contracts = anchor["contracts"]
    factory, controller, venue, implementation, asset = [address(contracts[name]) for name in ("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC")]
    if anchor.get("venue", {}).get("model") != "sdk" or address(anchor["venue"]["address"]) != venue:
        raise SetupError("UNSUPPORTED_DEPLOYMENT", "This release supports the reviewed SDK approval contracts only.")
    rpc.prefetch([("eth_getCode", [contracts[name], fixed]) for name in ("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC")])
    for name in ("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC"):
        code = code_bytes(rpc.call("eth_getCode", [contracts[name], fixed]))
        if not code or "0x" + keccak(code).hex() != anchor["codeHashes"][name].lower():
            raise SetupError("CONTRACT_CODE_MISMATCH", "Contract code does not match the reviewed release. No setup data was accepted.")
    rpc.prefetch_words([(factory, "settings()", []), (controller, "factory()", []), (factory, "venue()", []),
        (factory, "asset()", []), (factory, "defaultImplementation()", []), (controller, "isVault(address)", [vault]),
        (vault, "venue()", []), (vault, "asset()", []), (venue, "asset()", []), (venue, "operator()", []),
        (vault, "owner()", []), (venue, "coreAccountOf(address)", [vault]),
        (vault, "lifecycle()", []), (vault, "pendingRequestCount()", [])], fixed)
    read = lambda to, signature, args=(), count=1: rpc.words(to, signature, list(args), fixed, count)
    identity = lambda to, signature, args=(): decoded_address(read(to, signature, args)[0])
    if identity(factory, "settings()") != controller or identity(controller, "factory()") != factory or identity(factory, "venue()") != venue or identity(factory, "asset()") != asset or identity(factory, "defaultImplementation()") != implementation:
        raise SetupError("FACTORY_BINDING_MISMATCH", "The reviewed factory and controller bindings do not match.")
    if read(controller, "isVault(address)", [vault]) != [1]:
        raise SetupError("UNREGISTERED_VAULT", "This vault does not belong to the reviewed 2n20 factory.")
    clone = bytes.fromhex("363d3d373d3d3d363d73" + implementation[2:] + "5af43d82803e903d91602b57fd5bf3")
    if code_bytes(rpc.call("eth_getCode", [vault, fixed])) != clone:
        raise SetupError("UNSUPPORTED_VAULT", "This vault does not use the reviewed vault implementation.")
    if identity(vault, "venue()") != venue or identity(vault, "asset()") != asset or identity(venue, "asset()") != asset or identity(venue, "operator()") != address(anchor["venue"]["operator"]):
        raise SetupError("VAULT_BINDING_MISMATCH", "The vault, approval contract and deployment bindings do not match.")
    owner = identity(vault, "owner()")
    core = decoded_address(read(venue, "coreAccountOf(address)", [vault])[0], True)
    book, key, release, strikes, cuts_allowed_at, uncovered = read_account(rpc, venue, vault, core, fixed)
    lifecycle = read(vault, "lifecycle()")[0]
    pending_requests = read(vault, "pendingRequestCount()")[0]
    if lifecycle not in (0, 1, 2):
        raise SetupError("ACCOUNT_BINDING_MISMATCH", MISMATCH)
    if key.nonce == UINT64_MAX:
        raise SetupError("NONCE_EXHAUSTED", "This vault cannot request another trading key.")
    if core != ZERO:
        rpc.prefetch_words([(core, "vault()", []), (core, "venue()", []), (core, "asset()", [])], fixed)
        if not code_bytes(rpc.call("eth_getCode", [core, fixed])) or identity(core, "vault()") != vault or identity(core, "venue()") != venue or identity(core, "asset()") != asset:
            raise SetupError("ACCOUNT_BINDING_MISMATCH", "The trading account is not bound to this vault and approval contract.")
    found = Discovery(vault, network, canonical_link, anchor, owner, venue, core, key.requested, key.revocation_pending, key.nonce, number, block_hash, timestamp, book, key, release, strikes, cuts_allowed_at, lifecycle, pending_requests, uncovered)
    verify_current(found, rpc, now)
    return found


def verify_current(found: Discovery, rpc: Rpc, now: int | None = None):
    """Reject reorganizations and state changes before using public evidence."""
    chain, canonical, head = rpc.batch([("eth_chainId", []),
        ("eth_getBlockByNumber", [hex(found.block_number), False]), ("eth_getBlockByNumber", ["latest", False])])
    if integer(chain) != found.anchor["chainId"]:
        raise SetupError("CHAIN_CHANGED", "The chain changed during discovery. Try again.")
    if block_identity(canonical)[:2] != (found.block_number, found.block_hash):
        raise SetupError("CHAIN_CHANGED", "The chain changed during discovery. Try again.")
    head_number, head_hash, _ = block_identity(head, int(time.time()) if now is None else now)
    if head_number < found.block_number:
        raise SetupError("CHAIN_CHANGED", "The chain head moved backwards. Try again.")
    fixed = {"blockHash": head_hash, "requireCanonical": True}
    checks = [(found.vault, "venue()", [], [int(found.approval_contract, 16)]),
              (found.vault, "owner()", [], [int(found.owner, 16)]),
              (found.approval_contract, "keys(address)", [found.vault], list(found.key.words)),
              (found.approval_contract, "books(address)", [found.vault], list(found.book.words)),
              (found.vault, "lifecycle()", [], [found.lifecycle]),
              (found.vault, "pendingRequestCount()", [], [found.pending_requests]),
              (found.vault, "uncoveredAssets()", [], [found.uncovered])]
    rpc.prefetch_words([(target, signature, args) for target, signature, args, _ in checks], fixed)
    for target, signature, args, expected in checks:
        if rpc.words(target, signature, args, fixed, len(expected)) != expected:
            raise SetupError("STATE_CHANGED", "The vault state or approval nonce changed during verification. Try again before importing consent.")
    if block_identity(rpc.call("eth_getBlockByNumber", [hex(head_number), False]))[:2] != (head_number, head_hash):
        raise SetupError("CHAIN_CHANGED", "The chain changed during verification. Try again.")
    return fixed

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
    book: tuple
    observation: tuple
    key_status: tuple
    lifecycle: int
    pending_requests: int

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
    chain_id = integer(rpc.call("eth_chainId", []))
    if chain_id != anchor["chainId"] or chain_id != 999:
        raise SetupError("WRONG_CHAIN", "The public RPC is on the wrong chain. No key was created.")
    deployment_block = rpc.call("eth_getBlockByNumber", [hex(int(anchor["deploymentBlock"])), False])
    if not isinstance(deployment_block, dict) or not same_hash(deployment_block.get("hash"), anchor["genesisHash"]) or integer(deployment_block.get("number")) != int(anchor["deploymentBlock"]):
        raise SetupError("UNVERIFIED_DEPLOYMENT", "The deployment block does not match this release's reviewed trust anchor.")
    receipt = rpc.call("eth_getTransactionReceipt", [anchor["deploymentHash"]])
    if not isinstance(receipt, dict) or not same_hash(receipt.get("transactionHash"), anchor["deploymentHash"]) or not same_hash(receipt.get("blockHash"), anchor["genesisHash"]) or integer(receipt.get("status")) != 1 or integer(receipt.get("blockNumber")) != int(anchor["deploymentBlock"]) or address(receipt.get("contractAddress")) != address(anchor["contracts"]["VaultFactory"]):
        raise SetupError("UNVERIFIED_DEPLOYMENT", "The factory deployment receipt does not match the reviewed deployment.")
    current = rpc.call("eth_getBlockByNumber", ["latest", False])
    number, block_hash, timestamp = block_identity(current, int(time.time()) if now is None else now)
    fixed = {"blockHash": block_hash, "requireCanonical": True}
    contracts = anchor["contracts"]
    factory, controller, venue, implementation, asset = [address(contracts[name]) for name in ("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC")]
    if anchor.get("venue", {}).get("model") != "sdk" or address(anchor["venue"]["address"]) != venue:
        raise SetupError("UNSUPPORTED_DEPLOYMENT", "This release supports the reviewed SDK approval contracts only.")
    for name in ("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC"):
        code = code_bytes(rpc.call("eth_getCode", [contracts[name], fixed]))
        if not code or "0x" + keccak(code).hex() != anchor["codeHashes"][name].lower():
            raise SetupError("CONTRACT_CODE_MISMATCH", "Contract code does not match the reviewed release. No setup data was accepted.")
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
    if read(venue, "tradingKeyVersion()") != [2]:
        raise SetupError("UNSUPPORTED_CAPABILITY", "This approval contract does not support the required consent format.")
    owner = identity(vault, "owner()")
    core = decoded_address(read(venue, "coreAccountOf(address)", [vault])[0], True)
    book = tuple(read(venue, "book(address)", [vault], 4))
    observation = tuple(read(venue, "observationState(address)", [vault], 4))
    key = read(venue, "tradingKeys(address)", [vault], 3)
    key_status = tuple(read(venue, "tradingKeyStatus(address)", [vault], 4))
    lifecycle = read(vault, "lifecycle()")[0]
    pending_requests = read(vault, "pendingRequestCount()")[0]
    if decoded_address(book[0], True) != core or book[1] not in (0, 1, 2, 3) or book[2] != observation[0] or any(item > UINT64_MAX for item in (book[2], observation[0], observation[1], observation[2], key[2], key_status[1])) or key[1] not in (0, 1) or lifecycle not in (0, 1, 2):
        raise SetupError("ACCOUNT_BINDING_MISMATCH", "The public trading-account state is inconsistent.")
    for position in (0, 2, 3):
        decoded_address(key_status[position], True)
    requested = decoded_address(key[0], True)
    if key[2] == UINT64_MAX:
        raise SetupError("NONCE_EXHAUSTED", "This vault cannot request another trading key.")
    if core != ZERO:
        if not code_bytes(rpc.call("eth_getCode", [core, fixed])) or identity(core, "vault()") != vault or identity(core, "venue()") != venue or identity(core, "asset()") != asset:
            raise SetupError("ACCOUNT_BINDING_MISMATCH", "The trading account is not bound to this vault and approval contract.")
    found = Discovery(vault, network, canonical_link, anchor, owner, venue, core, requested, bool(key[1]), key[2], number, block_hash, timestamp, book, observation, key_status, lifecycle, pending_requests)
    verify_current(found, rpc, now)
    return found


def verify_current(found: Discovery, rpc: Rpc, now: int | None = None):
    """Reject reorganizations and state changes before using public evidence."""
    if integer(rpc.call("eth_chainId", [])) != found.anchor["chainId"]:
        raise SetupError("CHAIN_CHANGED", "The chain changed during discovery. Try again.")
    canonical = rpc.call("eth_getBlockByNumber", [hex(found.block_number), False])
    if block_identity(canonical)[:2] != (found.block_number, found.block_hash):
        raise SetupError("CHAIN_CHANGED", "The chain changed during discovery. Try again.")
    head_number, head_hash, _ = block_identity(rpc.call("eth_getBlockByNumber", ["latest", False]), int(time.time()) if now is None else now)
    if head_number < found.block_number:
        raise SetupError("CHAIN_CHANGED", "The chain head moved backwards. Try again.")
    fixed = {"blockHash": head_hash, "requireCanonical": True}
    checks = [(found.vault, "venue()", [], [int(found.approval_contract, 16)]),
              (found.vault, "owner()", [], [int(found.owner, 16)]),
              (found.approval_contract, "tradingKeys(address)", [found.vault], [int(found.requested_key, 16), int(found.revocation_pending), found.nonce]),
              (found.approval_contract, "book(address)", [found.vault], list(found.book)),
              (found.approval_contract, "tradingKeyStatus(address)", [found.vault], list(found.key_status)),
              (found.vault, "lifecycle()", [], [found.lifecycle]),
              (found.vault, "pendingRequestCount()", [], [found.pending_requests])]
    for target, signature, args, expected in checks:
        if rpc.words(target, signature, args, fixed, len(expected)) != expected:
            raise SetupError("STATE_CHANGED", "The vault state or approval nonce changed during verification. Try again before importing consent.")
    if block_identity(rpc.call("eth_getBlockByNumber", [hex(head_number), False]))[:2] != (head_number, head_hash):
        raise SetupError("CHAIN_CHANGED", "The chain changed during verification. Try again.")
    return fixed

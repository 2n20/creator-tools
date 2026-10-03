"""Disposable public deployment and RPC fixtures, never real keys."""

import copy
import time

from eth_abi import encode
from eth_utils import keccak

from twon20.discovery import ZERO, trust_anchor
from twon20.errors import SetupError

VAULT = "0x" + "a" * 40
OWNER = "0x" + "b" * 40
CORE = "0x" + "c" * 40
KEY = "0x" + "d" * 40
LINK = f"https://2n20.org/vaults/{VAULT}?network=mainnet"


class FakeRpc:
    def __init__(self):
        self.anchor = copy.deepcopy(trust_anchor("mainnet"))
        self.now = int(time.time())
        self.number = 50_000_000
        self.hash = "0x" + "1" * 64
        self.chain = 999
        self.fail = None
        self.reorg = False
        self.calls = []
        self.version = 2
        self.registered = 1
        self.core = CORE
        self.bound_venue = self.anchor["contracts"]["LocalVenue"].lower()
        self.key = [0, 0, 0]
        self.key_status = [0, 0, 0, 0]
        self.lifecycle = 0
        self.pending_requests = 0
        self.phase = 0
        self.used = False
        self.bad_digest = False
        self.bad_factory = False
        self.bad_core = False
        self.change_nonce = False
        self.codes = {}
        for index, name in enumerate(("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC")):
            code = bytes([0x60, index + 1])
            self.codes[self.anchor["contracts"][name].lower()] = "0x" + code.hex()
            self.anchor["codeHashes"][name] = "0x" + keccak(code).hex()
        implementation = self.anchor["contracts"]["Vault"][2:].lower()
        self.codes[VAULT] = "0x363d3d373d3d3d363d73" + implementation + "5af43d82803e903d91602b57fd5bf3"
        self.codes[CORE] = "0x6007"

    def call(self, method, params):
        self.calls.append((method, params))
        if self.fail == method:
            raise SetupError("RPC_UNAVAILABLE", "Public test fixture is unavailable.")
        if method == "eth_chainId":
            return hex(self.chain)
        if method == "eth_getBlockByNumber":
            if params[0] == hex(int(self.anchor["deploymentBlock"])):
                return {"number": params[0], "hash": self.anchor["genesisHash"], "timestamp": hex(self.now - 100)}
            return {"number": hex(self.number), "hash": "0x" + "2" * 64 if self.reorg and params[0] != "latest" else self.hash, "timestamp": hex(self.now)}
        if method == "eth_getTransactionReceipt":
            return {"transactionHash": self.anchor["deploymentHash"], "blockHash": self.anchor["genesisHash"], "blockNumber": hex(int(self.anchor["deploymentBlock"])), "status": "0x1", "contractAddress": self.anchor["contracts"]["VaultFactory"]}
        if method == "eth_getCode":
            return self.codes.get(params[0].lower(), "0x")
        raise AssertionError(method)

    def words(self, to, signature, arguments, block, count):
        self.calls.append((signature, arguments, block))
        if self.fail == signature:
            raise SetupError("RPC_UNAVAILABLE", "Public test fixture is unavailable.")
        contracts = self.anchor["contracts"]
        factory, controller, venue, implementation, asset = [contracts[name].lower() for name in ("VaultFactory", "PlatformController", "LocalVenue", "Vault", "MockUSDC")]
        scalar = {"settings()": int(controller, 16), "factory()": int(factory, 16), "defaultImplementation()": int(implementation, 16),
                  "asset()": int(asset, 16), "operator()": int(self.anchor["venue"]["operator"], 16), "owner()": int(OWNER, 16),
                  "tradingKeyVersion()": self.version, "isVault(address)": self.registered, "coreAccountOf(address)": int(self.core, 16),
                  "usedTradingKeys(address)": int(self.used), "lifecycle()": self.lifecycle, "pendingRequestCount()": self.pending_requests}
        if signature == "venue()":
            return [int(ZERO if self.bad_factory and to == factory else self.bound_venue, 16)]
        if signature == "vault()":
            return [int(OWNER if self.bad_core else VAULT, 16)]
        if signature == "book(address)":
            return [int(self.core, 16), self.phase, 7, 0]
        if signature == "observationState(address)":
            return [7, 9, self.now - 1, 0]
        if signature == "tradingKeys(address)":
            return [self.key[0], self.key[1], self.key[2] + int(self.change_nonce and block["blockHash"] != self.hash)]
        if signature == "tradingKeyStatus(address)":
            return self.key_status
        if signature == "consentDigest(address,address,uint64,uint64)":
            vault, key, nonce, deadline = arguments
            domain = keccak(encode(["bytes32", "bytes32", "bytes32", "uint256", "address"], [keccak(text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"), keccak(text="2n20 SDK venue"), keccak(text="1"), 999, venue]))
            struct = keccak(encode(["bytes32", "address", "address", "uint64", "uint64"], [keccak(text="TradingKeyConsent(address vault,address key,uint64 nonce,uint64 deadline)"), vault, key, nonce, deadline]))
            return [0 if self.bad_digest else int.from_bytes(keccak(b"\x19\x01" + domain + struct), "big")]
        if signature in scalar:
            return [scalar[signature]]
        raise AssertionError(signature)

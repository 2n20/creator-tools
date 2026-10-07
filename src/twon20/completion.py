"""Report software checks with the retained key. This grants no trading access."""

import os
import re
import time

from eth_account.messages import encode_typed_data

from .discovery import address, discover, vault_link
from .errors import SetupError
from .files import load_key, load_setup, open_output
from .onboarding import acquire_lock
from .rpc import Rpc, TransientReadError, request_json
from .status import status

SCHEMA = "2n20-software-setup-v1"
ENDPOINT = "https://2n20.org/api/creator-setup"
FIELDS = [("deploymentId", "string"), ("vaultAddress", "address"), ("ownerAddress", "address"),
          ("coreAccount", "address"), ("tradingKey", "address"), ("keyNonce", "uint256"),
          ("connectionChecked", "bool"), ("paperChecked", "bool"), ("issuedAt", "uint64"), ("deadline", "uint64")]


def typed_data(report):
    message = {name: int(report[name]) if kind.startswith("uint") else report[name] for name, kind in FIELDS}
    return {"domain": {"name": "2n20 Software Setup", "version": "1", "chainId": 999, "verifyingContract": report["vaultAddress"]},
            "primaryType": "SoftwareSetup",
            "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                     {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                      "SoftwareSetup": [{"name": name, "type": kind} for name, kind in FIELDS]},
            "message": message}


def complete_setup(link, directory, connection_checked, paper_checked, submit=None, now=None):
    if connection_checked is not True or paper_checked is not True:
        raise SetupError("SOFTWARE_CHECKS_REQUIRED", "Run the project's read-only connection and paper checks successfully before reporting them.")
    found = discover(link)
    output = open_output(directory)
    lock = None
    try:
        lock = acquire_lock(output)
        metadata = load_setup(output)
        if vault_link(metadata["vaultLink"])[2] != found.link or address(metadata["tradingKey"]) != found.requested_key:
            raise SetupError("KEY_CONFLICT", "Resume the setup directory that retains the current approved key. No key was replaced.")
        evidence = status(found, Rpc(found.network))
        if not evidence["tradingAccessReady"]:
            raise SetupError("ACCESS_UNVERIFIED", "Current public trading access is unverified. Resume the same onboarding command before reporting setup completion.")
        issued = int(time.time()) if now is None else now
        generation = found.anchor.get("generation")
        if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", generation):
            raise SetupError("UNSUPPORTED_DEPLOYMENT", "This CLI release cannot report setup completion for the deployment.")
        if abs(issued - found.timestamp) > 300:
            raise SetupError("CLOCK_MISMATCH", "Check the machine clock before reporting software setup.")
        report = {"schema": SCHEMA, "chainId": 999, "deploymentId": generation, "vaultAddress": found.vault,
                  "ownerAddress": found.owner, "coreAccount": found.trading_account, "tradingKey": found.requested_key,
                  "keyNonce": str(found.nonce), "connectionChecked": True, "paperChecked": True,
                  "issuedAt": str(issued), "deadline": str(issued + 900)}
        wallet = load_key(output, metadata)
        report["signature"] = "0x" + wallet.sign_message(encode_typed_data(full_message=typed_data(report))).signature.hex().removeprefix("0x")
        output.atomic_json("setup-report.public.json", report)
        limit = time.monotonic() + 90
        for attempt in range(3):
            try:
                acknowledgement = submit(ENDPOINT, report) if submit else request_json(ENDPOINT, report, timeout=min(30, limit - time.monotonic()))
                break
            except TransientReadError as error:
                delay = max((1, 3, 0)[attempt], error.retry_after)
                if attempt == 2 or limit - time.monotonic() <= delay + 1:
                    raise SetupError("REPORT_UNAVAILABLE", "Setup reporting is unavailable. Keep the same directory and retry complete-setup after the service recovers. Your checks and key are unchanged.") from None
                time.sleep(delay)
        completion = acknowledgement.get("completion") if isinstance(acknowledgement, dict) else None
        scope = ["deploymentId", "vaultAddress", "ownerAddress", "coreAccount", "tradingKey", "keyNonce"]
        if not isinstance(completion, dict) or any(completion.get(name) != report[name] for name in scope) or not isinstance(completion.get("completedAt"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", completion["completedAt"]):
            raise SetupError("INVALID_SETUP_ACKNOWLEDGEMENT", "The service did not acknowledge the current software setup. Retry from the same directory; do not claim setup is complete.")
        output.atomic_json("setup-completion.public.json", completion)
        return {"schema": "2n20-setup-completion-result-v1", "setupComplete": True, "completion": completion,
                "nextAction": "The vault page completes Step 3 automatically. Continue developing your strategy; enable live trading separately when you choose."}
    finally:
        if lock is not None:
            os.close(lock)
        output.close()

"""Resumable local setup and a public creator-wallet approval handoff."""

import fcntl
import json
import os
import re
import secrets
import shlex
import stat
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from eth_account import Account

from .consent import prepare, validate
from .discovery import ZERO, address, discover, vault_link
from .errors import SetupError
from .files import load_key, load_setup, new_output, open_output
from .git_safety import ensure_key_ignored
from .rpc import Rpc, parse_json, request_json
from .status import status

CHECKPOINT = "onboarding.public.json"
STATE_SCHEMA = "2n20-onboarding-state-v1"
HANDOFF_SCHEMA = "2n20-approval-handoff-v1"
HANDOFF_ENDPOINT = "https://2n20.org/api/sdk/approval-requests"
CONSENT_FILE = re.compile(r"consent(?:\.[0-9]+-[0-9a-f]+)?\.public\.json\Z")
CONFLICT_ERRORS = frozenset({
    "CHECKPOINT_CONFLICT", "DIRECTORY_CONFLICT", "KEY_CONFLICT", "KEY_NOT_FRESH",
    "KEY_ALREADY_USED", "KEY_MISSING", "CONSENT_CONFLICT", "TRACKED_KEY_PATH",
    "ONBOARDING_BUSY", "UNSAFE_CHECKPOINT", "UNSAFE_LOCK", "INVALID_PRIVATE_FILE",
    "KEY_RECORD_MISMATCH", "INVALID_VAULT_LINK", "INVALID_ADDRESS", "UNSUPPORTED_NETWORK",
    "WRONG_CHAIN", "UNVERIFIED_DEPLOYMENT", "UNSUPPORTED_DEPLOYMENT", "CONTRACT_CODE_MISMATCH",
    "FACTORY_BINDING_MISMATCH", "UNREGISTERED_VAULT", "UNSUPPORTED_VAULT",
    "VAULT_BINDING_MISMATCH", "UNSUPPORTED_CAPABILITY", "ACCOUNT_BINDING_MISMATCH",
    "NONCE_EXHAUSTED", "CONSENT_FORMAT_MISMATCH", "CONSENT_BINDING_MISMATCH",
    "INVALID_CONSENT", "INVALID_EXPIRY", "INVALID_SIGNATURE", "UNSAFE_DIRECTORY_PERMISSIONS",
    "UNSAFE_FILE_PERMISSIONS", "UNSAFE_GITIGNORE", "INVALID_SETUP_FILE",
    "BINDING_CHANGED", "OWNER_CHANGED", "INVALID_BINDING", "INVALID_PAYLOAD", "UNSUPPORTED_CONSENT",
})
HANDOFF_ERRORS = {
    "HANDOFF_EXPIRED": "The previous public approval link expired. Its identifier cannot be reused.",
    "CONSENT_EXPIRED": "The service found expired consent. Verify the machine clock and renew in this same setup directory.",
    "NONCE_CHANGED": "The service found a changed approval nonce. Resume this same command to verify current contract state before renewing.",
    "KEY_REQUESTED": "This key may have been requested while the handoff was submitted. Resume this same command to verify authoritative trading access.",
    "REVOCATION_PENDING": "A revocation may be pending. Resume this same command to verify current contract state.",
    "BINDING_CHANGED": "The service found changed vault or trading-account bindings. Resume only after resolving the binding conflict.",
    "OWNER_CHANGED": "The service found a changed creator wallet. Resolve the creator-wallet binding before resuming.",
    "KEY_NOT_FRESH": "The service rejected this key as already used or ineligible. Keep the retained key; resolve the conflict before continuing.",
    "INVALID_BINDING": "The service rejected the public account bindings. No approval handoff was accepted.",
    "INVALID_PAYLOAD": "The service rejected the public request format. Check the installed CLI version before resuming.",
    "INVALID_CONSENT": "The service rejected public consent. No approval handoff was accepted.",
    "INVALID_EXPIRY": "The service rejected the consent expiry. Check the machine clock and renew consent in this same directory.",
    "INVALID_SIGNATURE": "The service rejected the public consent signature. No approval handoff was accepted.",
    "UNSUPPORTED_NETWORK": "The service does not support this deployment network.",
    "UNSUPPORTED_CONSENT": "The service could not verify the supported contract consent format.",
    "HANDOFF_UNAVAILABLE": "The public approval service is unavailable. Keep this setup directory and use its public consent file or retry later.",
    "RATE_LIMITED": "The public approval service is rate limited. Keep this setup directory and retry later.",
}


def result(stage, code, directory, **detail):
    artifacts = {name: detail[field] for name, field in (("consent", "publicConsentFile"), ("configuration", "publicConfigFile"), ("status", "publicStatusFile"), ("checkpoint", "publicCheckpointFile")) if detail.get(field)}
    action = None
    if stage == "approval_required":
        action = {"kind": "creator_wallet_approval", "approvalUrl": detail["approvalUrl"]} if detail.get("approvalUrl") else {"kind": "public_consent_import", "publicConsentFile": detail.get("publicConsentFile")}
    elif stage == "selection_required":
        action = {"kind": "select_existing_directory", "directories": detail.get("resumeDirectories", [])}
    elif stage == "consent_expired":
        command = shlex.join(["2n20", "renew", "--directory", str(directory), "--json"])
        detail["renewCommand"] = command
        action = {"kind": "renew_consent", "command": command}
    return {"schema": "2n20-onboarding-result-v1", "stage": stage, "exitCode": code,
            "directory": str(directory), "privateFile": "strategy.key stays on this machine; never inspect, paste or upload it.",
            "requiredHumanAction": action, "publicEvidence": detail.get("status", {"evidence": "unavailable", "tradingAccessReady": False}),
            "publicArtifacts": artifacts, **detail}, code


def default_directory(link, cwd=None):
    vault, network, _ = vault_link(link)
    return (Path.cwd() if cwd is None else cwd) / f"2n20-setup-{network}-{vault[2:]}"


def retained_public_artifacts(output, state):
    fields = {}
    if output is None:
        return fields
    try:
        for name, field in (("config.public.json", "publicConfigFile"), ("status.public.json", "publicStatusFile"), (CHECKPOINT, "publicCheckpointFile")):
            if output.exists(name):
                fields[field] = str(output.path / name)
        if state is not None and state.get("consentFile") and output.exists(state["consentFile"]):
            fields["publicConsentFile"] = str(output.path / state["consentFile"])
        if state is not None and state.get("tradingKey"):
            fields["tradingKey"] = state["tradingKey"]
    except OSError:
        pass
    return fields


def legacy_candidates(link, cwd=None):
    _, _, canonical = vault_link(link)
    candidates = []
    parent = Path.cwd() if cwd is None else cwd
    entries = list(parent.iterdir())
    if len(entries) > 200:
        raise SetupError("SELECTION_REQUIRED", "This working directory has too many entries to rule out an existing setup. Choose its known setup directory explicitly with --directory; no new key was created.")
    for path in entries:
        if path.is_symlink() or not path.is_dir() or not (path / "setup.public.json").is_file():
            continue
        try:
            output = open_output(path)
            try:
                metadata = load_setup(output)
                if vault_link(metadata["vaultLink"])[2] == canonical:
                    candidates.append(str(output.path))
            finally:
                output.close()
        except (OSError, ValueError, SetupError):
            continue
    return sorted(candidates)


def acquire_lock(output):
    descriptor = os.open("onboarding.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=output.descriptor)
    info = os.fstat(descriptor)
    try:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SetupError("UNSAFE_LOCK", "The onboarding lock is not a safe local file.")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError:
        os.close(descriptor)
        raise SetupError("ONBOARDING_BUSY", "Another onboarding command owns this setup directory. Wait for it to finish, then resume the same directory.") from None
    except BaseException:
        os.close(descriptor)
        raise


def checkpoint(output, state, stage, **updates):
    state.update(updates)
    state["stage"] = stage
    output.atomic_json(CHECKPOINT, state)


def load_state(output, found):
    if output.exists(CHECKPOINT):
        try:
            state = parse_json(output.read(CHECKPOINT, 8192))
        except ValueError:
            raise SetupError("CHECKPOINT_CONFLICT", "The public onboarding checkpoint is invalid. No key was changed.") from None
        allowed = {"schema", "vaultLink", "owner", "tradingAccount", "tradingKey", "stage", "consentFile", "handoff", "lastError", "recoveringMissingKey"}
        if not isinstance(state, dict) or set(state) - allowed or state.get("schema") != STATE_SCHEMA or vault_link(state.get("vaultLink", ""))[2] != found.link:
            raise SetupError("CHECKPOINT_CONFLICT", "This directory belongs to a different vault or unsupported checkpoint.")
        if address(state.get("owner")) != found.owner or address(state.get("tradingAccount"), True) not in (ZERO, found.trading_account):
            raise SetupError("CHECKPOINT_CONFLICT", "The creator or trading-account binding changed. No key or approval handoff was replaced.")
        if state.get("tradingKey") is not None:
            address(state["tradingKey"])
        if not isinstance(state.get("stage"), str) or type(state.get("recoveringMissingKey")) is not bool or "tradingKey" not in state or "handoff" not in state or state["handoff"] is not None and not isinstance(state["handoff"], dict):
            raise SetupError("CHECKPOINT_CONFLICT", "The public checkpoint contains unsupported recovery fields. No key was changed.")
        if (state.get("consentFile") is not None and not isinstance(state["consentFile"], str)) or (isinstance(state.get("consentFile"), str) and not CONSENT_FILE.fullmatch(state["consentFile"])):
            raise SetupError("CHECKPOINT_CONFLICT", "The checkpoint does not reference a public consent file.")
        return state
    recognized = output.exists("setup.public.json") or output.exists("key-reservation.public.json")
    if not recognized and any(Path(output.path).iterdir()):
        # The lock itself is expected; other existing files need an explicit record.
        if any(path.name != "onboarding.lock" for path in output.path.iterdir()):
            raise SetupError("DIRECTORY_CONFLICT", "This existing directory is not a recognized 2n20 setup. Choose a new directory; no private files were read.")
    if output.exists("setup.public.json"):
        metadata = load_setup(output)
        if vault_link(metadata["vaultLink"])[2] != found.link:
            raise SetupError("DIRECTORY_CONFLICT", "The existing setup belongs to another vault. Its key was not read or changed.")
    elif output.exists("key-reservation.public.json"):
        value = parse_json(output.read("key-reservation.public.json", 2000))
        if not isinstance(value, dict) or set(value) != {"schema", "vaultLink"} or value["schema"] != "2n20-key-reservation-v1" or vault_link(value["vaultLink"])[2] != found.link:
            raise SetupError("DIRECTORY_CONFLICT", "The local key reservation belongs to another setup.")
    if output.exists("setup.public.json") and not output.exists("strategy.key"):
        raise SetupError("KEY_MISSING", "This existing setup records a trading key whose private file is missing. Restore that key locally; onboarding will not generate a replacement.")
    state = {"schema": STATE_SCHEMA, "vaultLink": found.link, "owner": found.owner,
             "tradingAccount": found.trading_account, "tradingKey": None, "stage": "preparing",
             "consentFile": None, "handoff": None, "recoveringMissingKey": False}
    checkpoint(output, state, "preparing")
    return state


def restore_key(output, found, state):
    ensure_key_ignored(output.path)
    if output.exists("strategy.key"):
        if output.exists("setup.public.json") or output.exists("setup.recovered.public.json"):
            metadata = load_setup(output)
            wallet = load_key(output, metadata)
        else:
            wallet = read_reserved_key(output)
            output.json("setup.public.json", {"schema": "2n20-local-setup-v1", "vaultLink": found.link, "tradingKey": wallet.address})
    else:
        if state["tradingKey"] is not None:
            raise SetupError("KEY_MISSING", "The recorded private key is missing. Restore it locally; onboarding will not replace it.")
        if found.requested_key != ZERO:
            raise SetupError("KEY_CONFLICT", "This vault already requests a trading key. Choose the directory that retains that exact key; onboarding will not generate a competing key.")
        if found.revocation_pending:
            raise SetupError("REVOCATION_PENDING", "A revocation is pending. Wait for it to complete, then resume this same directory; no key was generated.")
        if output.exists("setup.public.json"):
            raise SetupError("KEY_MISSING", "The recorded private key is missing. Restore it locally; onboarding will not replace it.")
        if not output.exists("key-reservation.public.json"):
            output.json("key-reservation.public.json", {"schema": "2n20-key-reservation-v1", "vaultLink": found.link})
        wallet = Account.create()
        output.write("strategy.key", wallet.key.hex().removeprefix("0x") + "\n")
        output.json("setup.public.json", {"schema": "2n20-local-setup-v1", "vaultLink": found.link, "tradingKey": wallet.address})
    key = address(wallet.address)
    if state["tradingKey"] is not None and address(state["tradingKey"]) != key:
        raise SetupError("KEY_CONFLICT", "The private key does not match the existing onboarding record. No files were replaced.")
    checkpoint(output, state, state["stage"], tradingKey=wallet.address, tradingAccount=found.trading_account, recoveringMissingKey=False)
    return wallet


def read_reserved_key(output):
    # Called only after verifying a public reservation/checkpoint for this vault.
    text = output.read("strategy.key", 128).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
        raise SetupError("INVALID_PRIVATE_FILE", "The retained local key is incomplete. Restore it locally; it will not be overwritten or displayed.")
    try:
        return Account.from_key(text)
    except ValueError:
        raise SetupError("INVALID_PRIVATE_FILE", "The retained local key is invalid. It was not overwritten or displayed.") from None


def consent_for(output, found, state, wallet, rpc):
    selected = state.get("consentFile")
    candidates = [selected] if selected else ["consent.public.json"]
    candidates.extend(sorted((path.name for path in output.path.iterdir() if CONSENT_FILE.fullmatch(path.name) and path.name not in candidates), reverse=True))
    last_error = None
    for name in candidates[:100]:
        if not output.exists(name):
            continue
        try:
            value = validate(output.read(name, 2000), found, rpc)
            if address(value["key"]) != address(wallet.address):
                raise SetupError("CONSENT_CONFLICT", "The public consent file is for a different key. No handoff was submitted.")
            changed = selected != name
            checkpoint(output, state, "approval_required", consentFile=name, handoff=None if changed else state.get("handoff"))
            return value
        except SetupError as error:
            if error.code not in ("EXPIRED_CONSENT", "STALE_NONCE"):
                raise
            last_error = error
    if last_error:
        raise last_error
    value = prepare(found, wallet, rpc)
    output.json("consent.public.json", value)
    checkpoint(output, state, "approval_required", consentFile="consent.public.json", handoff=None)
    return value


def verify_handoff(value, found, consent, now=None):
    now = int(time.time()) if now is None else now
    required = {"schema", "requestId", "approvalUrl", "expiresAt", "vault", "chainId", "key", "owner", "tradingAccount", "state"}
    if not isinstance(value, dict) or set(value) != required or value["schema"] != HANDOFF_SCHEMA or value["state"] != "pending" or not isinstance(value["requestId"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["requestId"]) or type(value["chainId"]) is not int or value["chainId"] != found.anchor["chainId"]:
        raise SetupError("INVALID_HANDOFF", "The approval service returned an unsupported public handoff. Use the public consent file instead.")
    if any(address(value[name]) != expected for name, expected in (("vault", found.vault), ("key", address(consent["key"])), ("owner", found.owner), ("tradingAccount", found.trading_account))) or type(value["expiresAt"]) is not int or not 0 < value["expiresAt"] <= int(consent["deadline"]):
        raise SetupError("INVALID_HANDOFF", "The approval handoff does not match verified local consent and account bindings.")
    try:
        parsed = urlsplit(value["approvalUrl"])
        query = parse_qs(parsed.query, strict_parsing=True)
    except (TypeError, ValueError):
        raise SetupError("INVALID_HANDOFF", "The approval service did not return the official vault approval link.") from None
    if parsed.scheme != "https" or parsed.netloc != "2n20.org" or parsed.fragment or parsed.path.lower() != f"/vaults/{found.vault}" or query != {"network": [found.network], "step": ["2"], "approval": [value["requestId"]]}:
        raise SetupError("INVALID_HANDOFF", "The approval service did not return the official vault approval link.")
    if value["expiresAt"] <= now:
        raise SetupError("HANDOFF_EXPIRED", "The public approval link expired. Renew eligible consent with the retained key before creating a replacement link.")
    return value


def handoff(output, found, state, consent, submit=None):
    if state.get("handoff") is not None:
        return verify_handoff(state["handoff"], found, consent)
    payload = {"consent": consent, "owner": found.owner, "tradingAccount": found.trading_account}
    if len(json.dumps(payload).encode()) > 4096:
        raise SetupError("INVALID_HANDOFF", "The public approval request exceeds its supported size.")
    value = submit(HANDOFF_ENDPOINT, payload) if submit else request_json(HANDOFF_ENDPOINT, payload, error_messages=HANDOFF_ERRORS)
    value = verify_handoff(value, found, consent)
    checkpoint(output, state, "approval_required", handoff=value)
    return value


def renew_expired_handoff(output, found, state, wallet, rpc, previous):
    # Fresh contract verification in prepare prevents renewing a used key or nonce.
    consent = prepare(found, wallet, rpc)
    if consent == previous:
        raise SetupError("CLOCK_MISMATCH", "The approval service expired this consent before the local clock did. Check the machine clock, then resume; no new key was created.")
    name = f"consent.{int(time.time())}-{secrets.token_hex(4)}.public.json"
    output.json(name, consent)
    checkpoint(output, state, "approval_required", consentFile=name, handoff=None)
    return consent


def public_artifacts(output, found, key, report):
    configuration = found.public() | {"localTradingKey": key, "tradingAccessReady": report["tradingAccessReady"], "strategyConnectionTested": False}
    output.atomic_json("config.public.json", configuration)
    output.atomic_json("status.public.json", report)
    return {"publicConfigFile": str(output.path / "config.public.json"),
            "publicStatusFile": str(output.path / "status.public.json"),
            "publicCheckpointFile": str(output.path / CHECKPOINT), "status": report}


def process(output, found, state, wallet):
    key = address(wallet.address)
    rpc = Rpc(found.network)
    if found.requested_key not in (ZERO, key):
        raise SetupError("KEY_CONFLICT", "The vault currently requests another trading key. No key or approval handoff was replaced.")
    report = status(found, rpc)
    artifacts = public_artifacts(output, found, wallet.address, report)
    if found.requested_key != ZERO:
        if report["tradingAccessReady"] and report["requestedTradingKey"].lower() == key:
            checkpoint(output, state, "ready")
            return result("ready", 0, output.path, tradingKey=wallet.address, strategyConfig=found.public(),
                          strategyConnectionTested=False, **artifacts, nextAction="Configure your existing strategy with tradingAccount and your retained local key, then run its own connection check.")
        checkpoint(output, state, "approval_pending")
        return result("approval_pending", 2, output.path, tradingKey=wallet.address,
                      reasons=report["reasons"], **artifacts, nextAction="Wait for verified public trading access, then resume this same command. Do not generate another key.")
    if found.revocation_pending:
        checkpoint(output, state, "approval_pending")
        return result("approval_pending", 2, output.path, tradingKey=wallet.address, **artifacts,
                      nextAction="Wait for the pending revocation to complete, then resume this same command. Do not generate another key.")
    consent = consent_for(output, found, state, wallet, rpc)
    public_path = str(output.path / state["consentFile"])
    if found.trading_account == ZERO:
        checkpoint(output, state, "funding_pending")
        return result("funding_pending", 2, output.path, publicConsentFile=public_path, **artifacts,
                      nextAction="Wait for the trading account to finish funding, then resume this same command. The local key and public consent were retained.")
    renewing = False
    try:
        try:
            request = handoff(output, found, state, consent)
        except SetupError as error:
            if error.code != "HANDOFF_EXPIRED":
                raise
            renewing = True
            consent = renew_expired_handoff(output, found, state, wallet, rpc, consent)
            renewing = False
            public_path = str(output.path / state["consentFile"])
            request = handoff(output, found, state, consent)
        return result("approval_required", 2, output.path, publicConsentFile=public_path,
                      approvalUrl=request["approvalUrl"], requestId=request["requestId"], expiresAt=request["expiresAt"],
                      **artifacts, nextAction="Open the approval link and approve with your creator wallet. Then resume this same command on the strategy machine.")
    except SetupError as error:
        if renewing:
            raise
        if error.code in ("KEY_REQUESTED", "REVOCATION_PENDING"):
            current = discover(found.link)
            if current.owner != address(state["owner"]) or address(state["tradingAccount"], True) not in (ZERO, current.trading_account):
                raise SetupError("CHECKPOINT_CONFLICT", "The creator or trading-account binding changed while the handoff was submitted.")
            if current.requested_key != ZERO or current.revocation_pending:
                return process(output, current, state, wallet)
            raise SetupError("STATE_CHANGED", "The service reported changed key state that could not be verified on-chain. Resume this same command before continuing.")
        if error.code in CONFLICT_ERRORS or error.code in ("CONSENT_EXPIRED", "NONCE_CHANGED"):
            raise
        checkpoint(output, state, "approval_required", lastError=error.code)
        return result("approval_required", 2, output.path, publicConsentFile=public_path, handoffUnavailable=True,
                      errorCode=error.code, **artifacts, nextAction="Import this public consent file into Step 2, or resume this same command to retry the identical public handoff. Keep the retained key on this machine.")


def onboard(link, directory=None, wait=0):
    if type(wait) is not int or not 0 <= wait <= 300:
        return result("conflict", 4, directory or Path.cwd(), errorCode="INVALID_WAIT", nextAction="Choose a bounded polling budget from 0 to 300 seconds.")
    response, code = _onboard(link, directory, wait)
    try:
        canonical = vault_link(link)[2]
    except SetupError:
        return response, code
    response["vaultLink"] = canonical
    command = lambda path: shlex.join(["2n20", "onboard", "--vault", canonical, "--directory", str(path), "--json"])
    if response["stage"] == "selection_required":
        response["resumeCommands"] = [command(path) for path in response.get("resumeDirectories", [])]
    else:
        response["resumeCommand"] = command(response["directory"])
    return response, code


def _onboard(link, directory=None, wait=0):
    if directory is None:
        try:
            directory = default_directory(link)
            choices = legacy_candidates(link) if not directory.exists() else []
        except SetupError as error:
            return result("selection_required" if error.code == "SELECTION_REQUIRED" else "conflict", 2 if error.code == "SELECTION_REQUIRED" else 4, Path.cwd(), errorCode=error.code, nextAction=error.message)
        if not directory.exists():
            if choices:
                return result("selection_required", 2, directory, resumeDirectories=choices,
                              nextAction="Resume your existing setup with --directory <one of the listed paths>. No new key was created.")
    directory = Path(directory).expanduser().absolute()
    output = None
    lock = None
    state = None
    try:
        found = discover(link)
        if directory.exists() or directory.is_symlink():
            output = open_output(directory)
        else:
            output = new_output(found, directory)
        lock = acquire_lock(output)
        state = load_state(output, found)
        wallet = restore_key(output, found, state)
        deadline = time.monotonic() + wait
        delay = 2
        while True:
            response, code = process(output, found, state, wallet)
            if code == 0 or time.monotonic() >= deadline or response["stage"] not in ("approval_required", "approval_pending", "funding_pending"):
                return response, code
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
            delay = min(delay * 2, 10)
            found = discover(link)
            if found.owner != address(state["owner"]) or address(state["tradingAccount"], True) not in (ZERO, found.trading_account):
                raise SetupError("CHECKPOINT_CONFLICT", "The creator or trading-account binding changed during polling. Resume only after resolving that change.")
    except KeyboardInterrupt:
        return result("interrupted", 130, directory, **retained_public_artifacts(output, state), nextAction="Resume this same command. Any created key, consent and approval handoff were retained.")
    except SetupError as error:
        stage, code = ("consent_expired", 2) if error.code in ("EXPIRED_CONSENT", "STALE_NONCE", "CONSENT_EXPIRED", "NONCE_CHANGED") else ("approval_pending", 2) if error.code == "REVOCATION_PENDING" else ("conflict", 4) if error.code in CONFLICT_ERRORS else ("retry_required", 3)
        if output is not None and state is not None and lock is not None:
            try:
                checkpoint(output, state, stage, lastError=error.code)
            except (SetupError, OSError):
                pass
        action = error.message
        if stage == "consent_expired":
            action = "Renew public consent using `2n20 renew --directory <this setup directory>`, then resume this same onboarding command. The retained key is unchanged."
        return result(stage, code, directory, errorCode=error.code, **retained_public_artifacts(output, state), nextAction=action)
    except (OSError, ValueError, TypeError, KeyError):
        return result("retry_required", 3, directory, **retained_public_artifacts(output, state), nextAction="Public verification or local persistence failed. Resume this same command; existing keys and public files were not overwritten.")
    finally:
        if lock is not None:
            os.close(lock)
        if output is not None:
            output.close()

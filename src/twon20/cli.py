"""The maintained 2n20 creator setup command."""

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

from . import __version__
from .consent import prepare, validate
from .discovery import ZERO, discover
from .errors import SetupError
from .files import load_key, load_setup, new_output, open_output, read_public, retain_key
from .rpc import Rpc
from .status import status
from .onboarding import onboard
from .skill_export import export_skill
from .init import init_project


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="2n20", description="Prepare local trading-key consent and verify public vault configuration. No transactions or trades are submitted.")
    root.add_argument("--version", action="version", version=f"2n20 {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    starter = commands.add_parser("init", help="Create a new Hyperliquid Python starter project without installing dependencies or creating keys")
    starter.add_argument("directory", type=Path, help="A new project directory under an existing parent")
    starter.add_argument("--template", required=True, choices=("hyperliquid-python",))
    starter.add_argument("--json", action="store_true", help="Print public project files and next commands as JSON")
    guided = commands.add_parser("onboard", help="Resume local key setup, creator-wallet approval and verified trading access")
    guided.add_argument("--vault", required=True)
    guided.add_argument("--directory", type=Path, help="Explicitly choose a new or existing setup directory; otherwise use the same vault-scoped directory here")
    guided.add_argument("--wait", type=int, default=0, help="Bounded polling budget in seconds, from 0 to 300 (default 0)")
    guided.add_argument("--json", action="store_true", help="Print the stable public stage, exit code and resume instructions")
    skill = commands.add_parser("skill", help="Explicitly export the bundled portable 2n20-setup skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    export = skill_commands.add_parser("export")
    export.add_argument("--directory", required=True, type=Path, help="A new directory to receive SKILL.md; its parent must exist")
    export.add_argument("--json", action="store_true")
    setup = commands.add_parser("setup", help="Create a fresh private key locally and its public consent file")
    setup.add_argument("--vault", required=True, help="The https://2n20.org/vaults/... link copied from Step 2")
    setup.add_argument("--output", type=Path, help="A new private directory under an existing parent; defaults to a unique directory here")
    setup.add_argument("--json", action="store_true", help="Print public result metadata as JSON")
    renew = commands.add_parser("renew", help="Renew public consent using the key retained in your setup directory")
    renew.add_argument("--directory", required=True, type=Path, help="The directory previously created by 2n20 setup")
    renew.add_argument("--json", action="store_true")
    for command, help_text in (("status", "Verify public trading access without placing an order"), ("config", "Print verified public configuration for your existing strategy"), ("check-consent", "Check a public consent file against fresh contract evidence")):
        subparser = commands.add_parser(command, help=help_text)
        subparser.add_argument("--vault", required=True)
        if command == "check-consent":
            subparser.add_argument("--consent", required=True, type=Path, help="Only the consent.public.json file")
        subparser.add_argument("--json", action="store_true", help="Print public JSON (config always prints JSON)")
    return root


def setup(args) -> dict:
    found = discover(args.vault)
    rpc = Rpc(found.network)
    output = new_output(found, args.output)
    try:
        wallet = retain_key(output, found)
        consent = prepare(found, wallet, rpc)
        public_path = output.json("consent.public.json", consent)
        output.json("strategy.public.json", found.public())
        return {"directory": str(output.path), "publicConsentFile": str(public_path), "tradingKey": wallet.address,
                "expiresAt": int(consent["deadline"]), "nextAction": "Import only consent.public.json into Step 2 and approve with your creator wallet.",
                "privateFile": "strategy.key stays on this machine. Never import, paste or upload it."}
    except (SetupError, OSError, ValueError):
        raise SetupError("SETUP_INCOMPLETE", f"Setup did not complete. Keep the directory {output.path}; any created private key was retained unchanged. Resume with 2n20 onboard --vault '{found.link}' --directory '{output.path}'. No transaction was submitted.") from None
    finally:
        output.close()


def renew(args) -> dict:
    output = open_output(args.directory)
    try:
        metadata = load_setup(output)
        found = discover(metadata["vaultLink"])
        wallet = load_key(output, metadata)
        consent = prepare(found, wallet, Rpc(found.network))
        name = f"consent.{int(time.time())}-{secrets.token_hex(4)}.public.json"
        public_path = output.json(name, consent)
        return {"directory": str(output.path), "publicConsentFile": str(public_path), "tradingKey": wallet.address,
                "expiresAt": int(consent["deadline"]), "nextAction": "Import this new public file into Step 2. Older consent may be expired or stale.",
                "privateFile": "The existing strategy.key was not changed. Keep it on this machine."}
    finally:
        output.close()


def run(args) -> tuple[dict, int]:
    if args.command == "init":
        return init_project(args.directory, args.template), 0
    if args.command == "onboard":
        return onboard(args.vault, args.directory, args.wait)
    if args.command == "skill":
        return export_skill(args.directory), 0
    if args.command == "setup":
        return setup(args), 0
    if args.command == "renew":
        return renew(args), 0
    found = discover(args.vault)
    rpc = Rpc(found.network)
    if args.command == "config":
        if found.trading_account == ZERO:
            raise SetupError("TRADING_ACCOUNT_PENDING", "Wait for vault funding to create the trading account, then run config again.")
        return found.public() | {"nextAction": "Configure your existing strategy with tradingAccount as its account address and your local strategy.key as its signing key. Run your strategy's own connection check."}, 0
    if args.command == "check-consent":
        consent = validate(read_public(args.consent), found, rpc)
        return {"valid": True, "vault": found.vault, "approvalContract": found.approval_contract,
                "tradingKey": consent["key"], "nextApprovalNonce": consent["nonce"], "expiresAt": int(consent["deadline"]),
                "nextAction": "Import this public consent into Step 2. The app and contract verify the nonce and expiry again before approval."}, 0
    report = status(found, rpc)
    return report, 0 if report["tradingAccessReady"] else 2


def print_init_failure(args, code, message):
    if args.json:
        print(json.dumps({"schema": "2n20-project-init-v1", "directory": str(args.directory.expanduser().absolute()),
                          "template": args.template, "initialized": False, "errorCode": code,
                          "keysCreated": False, "dependenciesInstalled": False, "nextAction": message}, indent=2))
    else:
        print(f"{code}: {message}", file=sys.stderr)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result, exit_code = run(args)
        if args.json or args.command in ("config", "status"):
            print(json.dumps(result, indent=2))
        elif args.command == "init":
            print(f"Starter project: {result['directory']}")
            print(f"Template version: {result['templateVersion']}")
            for command in result["commands"].values():
                print(command)
            print(result["nextAction"])
        elif args.command == "onboard":
            print(f"Onboarding: {result['stage']}")
            if result.get("approvalUrl"):
                print(f"Creator-wallet approval: {result['approvalUrl']}")
            if result.get("publicConsentFile"):
                print(f"Public consent file: {result['publicConsentFile']}")
            for directory in result.get("resumeDirectories", []):
                print(f"Existing setup: {directory}")
            print(result["nextAction"])
            if result.get("resumeCommand"):
                print(f"Resume: {result['resumeCommand']}")
        elif args.command == "skill":
            print(f"Portable skill: {result['skillFile']}")
            print(result["nextAction"])
        elif args.command in ("setup", "renew"):
            print(f"Public consent file: {result['publicConsentFile']}")
            print(result["privateFile"])
            print(result["nextAction"])
            print("Consent expires in one hour. If it expires or the nonce changes, run 2n20 renew --directory <your-setup-directory> here and import the new public file.")
        else:
            print("Public consent signature, vault, approval contract, nonce and expiry verified.")
            print(result["nextAction"])
        return exit_code
    except SetupError as error:
        if args.command == "init":
            print_init_failure(args, error.code, error.message)
        else:
            print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        if args.command == "init":
            print_init_failure(args, "INIT_UNAVAILABLE", "Project generation could not complete. Check the parent directory and local permissions, then retry. Existing projects were not replaced.")
        else:
            print("SETUP_UNAVAILABLE: Setup could not complete. Keep any existing setup directory and key; no private material was displayed or overwritten. Check local permissions and retry. No transaction was submitted.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.command == "init":
            print_init_failure(args, "INIT_INTERRUPTED", "Project generation was interrupted. Retry the same command; existing projects were not replaced.")
        else:
            print("INTERRUPTED: Keep any created setup directory. Use 2n20 renew --directory <directory> to retry; existing keys were not overwritten.", file=sys.stderr)
        return 130

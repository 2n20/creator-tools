#!/usr/bin/env python3
"""Paper by default. Explicit previews and separately authorized live smoke."""
import argparse
import json
from pathlib import Path
import shlex
import sys

from risk import Bounds, Stop, entry_plan, number
from strategy import paper


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest="command")
    simulation = sub.add_parser("paper", help="Fictional fills; no signer, order or transfer")
    simulation.add_argument("--public-data", action="store_true")
    simulation.add_argument("--market", default="BTC")
    simulation.add_argument("--budget", default="12")
    for name in ("check", "live-smoke", "collateral", "recovery"):
        mode = sub.add_parser(name)
        mode.add_argument("--vault", required=True)
        mode.add_argument("--directory", required=True)
        mode.add_argument("--state-directory", default=".runner-state")
        mode.add_argument("--market", required=name == "live-smoke", default=None if name == "live-smoke" else "BTC")
        if name == "live-smoke":
            mode.add_argument("--side", required=True, choices=("buy", "sell"))
            mode.add_argument("--max-notional", required=True)
            mode.add_argument("--slippage-bps", type=int, required=True)
            mode.add_argument("--duration-seconds", type=int, required=True)
            mode.add_argument("--execute", choices=("LIVE_SMOKE",))
            mode.add_argument("--resume-close", action="store_true")
        elif name == "collateral":
            mode.add_argument("--amount", required=True)
            mode.add_argument("--execute", choices=("TRANSFER_TO_PERPS",))
        elif name == "recovery":
            mode.add_argument("--acknowledge", choices=("UNVERIFIED_COLLATERAL",))
            mode.add_argument("--request-nonce", type=int)
    return command


def run(args):
    if args.command in (None, "paper"):
        if getattr(args, "public_data", False):
            from adapter import public_candles
            closes = public_candles(args.market)
        else:
            value = json.loads(Path(__file__).with_name("fixture.candles.public.json").read_text(encoding="utf-8"))
            closes = value["closes"]
        return {"schema": "2n20-runner-result-v1", "stage": "paper_complete",
                "source": "public API" if getattr(args, "public_data", False) else "deterministic fixture",
                **paper(closes, getattr(args, "budget", "12"))}, 0
    from adapter import Access, Adapter, SDK_VERSION
    from execution import collateral, collateral_plan, review_collateral, smoke
    from journal import Journal, account_lock
    access = Access(args.vault, args.directory)
    try:
        adapter = Adapter(access)
        if args.command == "check":
            snapshot = adapter.snapshot(args.market)
            return {"schema": "2n20-runner-result-v1", "stage": "checked", "scope": access.scope,
                    "sdkVersion": SDK_VERSION, "keyPath": str(access.directory / "strategy.key"),
                    "signerLoaded": False, "ordersSubmitted": 0, "transfersSubmitted": 0,
                    "tradingAccessVerified": True, "strategyConnectionTested": "public SDK reads only",
                    "market": args.market, "sizeDecimals": snapshot["decimals"],
                    "spotUsdcAvailable": str(snapshot["spot_available"]), "perpUsdcAvailable": str(snapshot["perp_available"]),
                    "accountHasConflicts": bool(snapshot["positions"] or snapshot["orders"]),
                    "nextAction": "Paper remains the default. Review explicit limits in a live-smoke preview before separately authorizing orders."}, 0
        if args.command == "recovery":
            state_directory = Path(args.state_directory).expanduser().absolute()
            if not state_directory.exists():
                raise Stop("RECOVERY_MISSING", "Choose the original existing state directory. A recovery review will not create an experiment.")
            public_arguments = ["recovery", "--vault", access.vault, "--directory", str(access.directory),
                                "--state-directory", str(state_directory), "--market", args.market]
            if args.acknowledge: public_arguments.extend(["--acknowledge", args.acknowledge])
            if args.request_nonce is not None:
                if not 0 < args.request_nonce < 2**64:
                    raise Stop("RECOVERY_INTENT_MISMATCH", "Use the exact nonce shown in the retained public recovery review.")
                public_arguments.extend(["--request-nonce", str(args.request_nonce)])
            # The market is first verified by a public snapshot, never echoed
            # from an invalid prevalidation input or credential-bearing URL.
            adapter.snapshot(args.market)
            args.public_resume_command = shlex.join([sys.executable, str(Path(__file__).absolute()), *public_arguments])
            args.public_state_directory = str(state_directory)
            with account_lock(access.scope["tradingAccount"]) as guard:
                journal = Journal(state_directory, access.scope)
                try:
                    if journal.read("collateral.public.json") is None:
                        raise Stop("RECOVERY_MISSING", "There is no retained collateral request in this state directory.")
                    guard.reserve(journal, access.scope)
                    result = review_collateral(adapter, journal, args.market, bool(args.acknowledge), args.request_nonce)
                    if args.acknowledge: guard.clear()
                finally: journal.close()
            return result, 0 if args.acknowledge else 2
        bounds = Bounds(args.market, args.side, number(args.max_notional), args.slippage_bps, args.duration_seconds) if args.command == "live-smoke" else None
        if not bounds:
            amount = number(args.amount)
            if not 0 < amount <= 100 or amount.quantize(number("0.000001")) != amount:
                raise Stop("INVALID_TRANSFER_AMOUNT", "Choose an explicit bounded amount with at most six decimals.")
        state_directory = Path(args.state_directory).expanduser().absolute()
        public_arguments = [args.command, "--vault", access.vault, "--directory", str(access.directory),
                            "--state-directory", str(state_directory), "--market", args.market]
        if bounds:
            public_arguments.extend(["--side", bounds.side, "--max-notional", str(bounds.max_notional),
                                     "--slippage-bps", str(bounds.slippage_bps), "--duration-seconds", str(bounds.duration_seconds)])
        else:
            public_arguments.extend(["--amount", str(amount)])
        if args.execute: public_arguments.extend(["--execute", args.execute])
        if getattr(args, "resume_close", False): public_arguments.append("--resume-close")
        args.public_resume_command = shlex.join([sys.executable, str(Path(__file__).absolute()), *public_arguments])
        args.public_state_directory = str(state_directory)
        if not args.execute:
            if getattr(args, "resume_close", False):
                raise Stop("EXECUTION_AUTHORIZATION_REQUIRED", "Recovery also needs the deliberate --execute LIVE_SMOKE authorization.")
            snapshot = adapter.snapshot(args.market)
            plan = entry_plan(bounds, snapshot) if bounds else collateral_plan(snapshot, args.amount)
            return {"schema": "2n20-runner-result-v1", "stage": "preview", "scope": access.scope,
                    "mode": args.command, "plan": plan, "bounds": bounds.public() if bounds else None,
                    "signerLoaded": False, "ordersSubmitted": 0, "transfersSubmitted": 0,
                    "requiredHumanAction": "Review this explicit plan. Only a separate command with --execute " + ("LIVE_SMOKE" if bounds else "TRANSFER_TO_PERPS") + " authorizes it."}, 0
        with account_lock(access.scope["tradingAccount"]) as guard:
            journal = Journal(state_directory, access.scope)
            try:
                guard.reserve(journal, access.scope)
                other = journal.read("collateral.public.json" if bounds else "smoke.public.json")
                if other is not None and other.get("phase") not in {"balance_observed", "human_acknowledged", "complete", "entry_rejected"}:
                    raise Stop("OTHER_INTENT_UNRESOLVED", "This account has another unresolved action in the retained state directory. Reconcile that original command before authorizing a different action.")
                result = smoke(adapter, bounds, journal, args.resume_close) if bounds else collateral(adapter, journal, args.amount, args.market)
                # Observed balances cannot clear an unresolved transfer's
                # identity, even after a separately verified smoke round trip.
                if result["stage"] in {"complete", "entry_rejected"} and (other is None or bounds and other.get("phase") == "human_acknowledged"):
                    guard.clear()
            finally:
                journal.close()
        code = 0 if result["stage"] == "complete" else 2
        return result, code
    finally:
        access.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result, code = run(args)
    except Stop as error:
        result, code = {"schema": "2n20-runner-result-v1", "stage": "stopped", "errorCode": error.code, "nextAction": error.action,
                        "executionState": "unverified; preserve any existing public journal and reconcile the same intent"}, 3
        if error.recovery: result["publicRecovery"] = error.recovery
    except KeyboardInterrupt:
        result, code = {"schema": "2n20-runner-result-v1", "stage": "interrupted", "errorCode": "INTERRUPTED",
                        "nextAction": "Keep the state directory. Repeat the exact command to reconcile; never create another entry or transfer because a response was lost."}, 130
    except Exception:
        # Never echo untrusted provider exceptions, filesystem contents,
        # credentials, SDK signed payloads or Python tracebacks.
        result, code = {"schema": "2n20-runner-result-v1", "stage": "stopped", "errorCode": "CHECK_UNAVAILABLE",
                        "nextAction": "Check the reviewed installation, directory permissions and public CLI status. Preserve the retained key and journal; never blindly resubmit."}, 3
    if getattr(args, "public_resume_command", None):
        result["resumeCommand"] = args.public_resume_command
        result["stateDirectory"] = args.public_state_directory
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""A retained runtime reservation cannot be cleared by a finite diagnostic."""
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from journal import Journal, account_lock
from risk import Stop
from runner import parser, run
from test_runner import SCOPE, LINK, snapshot


class RuntimeReservationTests(unittest.TestCase):
    def test_legacy_commands_preserve_runtime_reservation(self):
        for command in ("live-smoke", "collateral", "recovery"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                state = root / "state"
                locks = root / "locks"
                with account_lock(SCOPE["tradingAccount"], locks) as guard:
                    journal = Journal(state, SCOPE)
                    journal.output.atomic_json("runtime.public.json", {"schema": "2n20-runtime-checkpoint-v1", "scope": SCOPE})
                    guard.reserve(journal, SCOPE)
                    journal.close()
                @contextlib.contextmanager
                def isolated_lock(account):
                    with account_lock(account, locks) as guard:
                        yield guard
                access = Mock(scope=SCOPE, vault=LINK, directory=root)
                adapter = Mock(); adapter.snapshot.return_value = snapshot()
                args = [command, "--vault", LINK, "--directory", str(root), "--state-directory", str(state)]
                if command == "live-smoke":
                    args += ["--market", "BTC", "--side", "buy", "--max-notional", "12", "--slippage-bps", "20", "--duration-seconds", "30", "--execute", "LIVE_SMOKE"]
                elif command == "collateral":
                    args += ["--amount", "5", "--execute", "TRANSFER_TO_PERPS"]
                else:
                    args += ["--acknowledge", "UNVERIFIED_COLLATERAL", "--request-nonce", "123"]
                with patch("adapter.Access", return_value=access), patch("adapter.Adapter", return_value=adapter), patch("journal.account_lock", isolated_lock), patch("execution.smoke") as smoke, patch("execution.collateral") as collateral, patch("execution.review_collateral") as recovery:
                    with self.assertRaises(Stop) as failure: run(parser().parse_args(args))
                    self.assertEqual(failure.exception.code, "RUNTIME_OWNS_DIRECTORY")
                    smoke.assert_not_called(); collateral.assert_not_called(); recovery.assert_not_called()
                with account_lock(SCOPE["tradingAccount"], locks) as guard:
                    other = Journal(root / "other", SCOPE)
                    try:
                        with self.assertRaises(Stop) as failure: guard.reserve(other, SCOPE)
                        self.assertEqual(failure.exception.code, "ACTIVE_INTENT")
                    finally: other.close()

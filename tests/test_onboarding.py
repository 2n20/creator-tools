import contextlib
import io
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eth_account import Account
from eth_account.messages import encode_typed_data

from twon20.cli import main
from twon20.consent import prepare, typed_data
from twon20.discovery import discover
from twon20.errors import SetupError
from twon20.files import Output, load_key, load_setup, new_output, open_output, retain_key
from twon20.onboarding import CHECKPOINT, HANDOFF_ENDPOINT, default_directory, onboard
from twon20.skill_export import export_skill

from support import CORE, LINK, OWNER, FakeRpc


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cwd = Path(self.temporary.name).resolve()
        self.directory = self.cwd / "setup"
        self.rpc = FakeRpc()
        self.requests = []
        self.service_receipts = {}
        self.ambiguous = False
        self.unavailable = False
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("twon20.onboarding.discover", side_effect=self.discover))
        self.stack.enter_context(patch("twon20.onboarding.Rpc", return_value=self.rpc))
        self.stack.enter_context(patch("twon20.onboarding.request_json", side_effect=self.service))
        self.stack.enter_context(patch("twon20.status.request_json", side_effect=self.info))

    def discover(self, link):
        return discover(link, self.rpc, self.rpc.now, self.rpc.anchor)

    def service(self, url, payload, **options):
        self.assertEqual(url, HANDOFF_ENDPOINT)
        self.assertEqual(set(payload), {"consent", "owner", "tradingAccount"})
        self.requests.append(payload)
        encoded = json.dumps(payload, sort_keys=True)
        if self.unavailable:
            raise SetupError("PUBLIC_READ_UNAVAILABLE", "Disposable service failure")
        request_id = hashlib.sha256(encoded.encode()).hexdigest()
        receipt = self.service_receipts.setdefault(encoded, {
            "schema": "2n20-approval-handoff-v1", "requestId": request_id,
            "approvalUrl": LINK + "&step=2&approval=" + request_id,
            "expiresAt": min(int(payload["consent"]["deadline"]), self.rpc.now + 3600), "vault": payload["consent"]["vault"],
            "chainId": 999, "key": payload["consent"]["key"], "owner": payload["owner"],
            "tradingAccount": payload["tradingAccount"], "state": "pending"})
        if self.ambiguous:
            self.ambiguous = False
            raise SetupError("PUBLIC_READ_UNAVAILABLE", "Response lost after service accepted public payload")
        return receipt

    def info(self, url, payload):
        key = f"0x{self.rpc.key[0]:040x}"
        return {"userRole": {"role": "agent", "data": {"user": CORE}},
                "extraAgents": [{"address": key, "name": "2n20", "validUntil": (self.rpc.now + 3600) * 1000}],
                "userAbstraction": "disabled", "spotClearinghouseState": {"balances": [{"token": 0, "coin": "USDC", "total": "3", "hold": "0"}]},
                "clearinghouseState": {"time": self.rpc.now * 1000, "marginSummary": {"accountValue": "0"}}}[payload["type"]]

    def run_onboard(self, directory=None, wait=0):
        return onboard(LINK, directory or self.directory, wait)

    def key_bytes(self):
        return (self.directory / "strategy.key").read_bytes()

    def test_terminal_only_ambiguous_handoff_nomination_and_verified_access(self):
        self.ambiguous = True
        first, code = self.run_onboard()
        self.assertEqual((first["stage"], code), ("approval_required", 2))
        self.assertTrue(first["handoffUnavailable"])
        retained = self.key_bytes()
        second, code = self.run_onboard()
        self.assertEqual((second["stage"], code), ("approval_required", 2))
        self.assertEqual(len(self.service_receipts), 1)
        self.assertEqual(self.requests[0], self.requests[1])
        third, code = self.run_onboard()
        self.assertEqual(third["requestId"], second["requestId"])
        self.assertEqual(len(self.requests), 2)
        key = self.requests[0]["consent"]["key"]
        self.rpc.key = [int(key, 16), 0, 1]
        pending, code = self.run_onboard()
        self.assertEqual((pending["stage"], code), ("approval_pending", 2))
        self.rpc.key_status = [int(key, 16), self.rpc.now + 3600, int(OWNER, 16), 0]
        ready, code = self.run_onboard()
        self.assertEqual((ready["stage"], code), ("ready", 0))
        self.assertEqual(ready["strategyConfig"]["tradingAccount"], CORE)
        self.assertEqual(ready["tradingKey"], key)
        self.assertFalse(ready["strategyConnectionTested"])
        self.assertTrue(ready["publicEvidence"]["tradingAccessReady"])
        self.assertIsNone(ready["requiredHumanAction"])
        self.assertTrue(Path(ready["publicArtifacts"]["configuration"]).is_file())
        self.assertTrue(Path(ready["publicArtifacts"]["status"]).is_file())
        self.assertEqual(self.key_bytes(), retained)
        self.assertEqual(len(self.requests), 2)
        transcript = json.dumps([first, second, third, pending, ready])
        self.assertNotIn(retained.decode().strip(), transcript)
        self.assertFalse(any(call[0] in ("eth_sendRawTransaction", "eth_sendTransaction") for call in self.rpc.calls))

    def test_default_repeats_resume_same_directory_without_duplicate_key_or_post(self):
        with patch("twon20.onboarding.Path.cwd", return_value=self.cwd):
            first, _ = onboard(LINK)
            key = (Path(first["directory"]) / "strategy.key").read_bytes()
            second, _ = onboard(LINK)
        self.assertEqual(first["directory"], second["directory"])
        self.assertEqual(first["requestId"], second["requestId"])
        self.assertEqual((Path(first["directory"]) / "strategy.key").read_bytes(), key)
        self.assertEqual(len(self.requests), 1)

    def test_legacy_selection_uses_only_public_metadata_then_explicit_adoption(self):
        output = new_output(self.discover(LINK), self.directory)
        try:
            wallet = retain_key(output, self.discover(LINK))
        finally:
            output.close()
        with patch("twon20.onboarding.Path.cwd", return_value=self.cwd), patch("twon20.onboarding.Account.create", side_effect=AssertionError("No new key")):
            selected, code = onboard(LINK)
            self.assertEqual((selected["stage"], code), ("selection_required", 2))
            self.assertEqual(selected["resumeDirectories"], [str(self.directory)])
            self.assertFalse(default_directory(LINK, self.cwd).exists())
            resumed, _ = self.run_onboard()
        self.assertEqual(self.requests[0]["consent"]["key"], wallet.address)
        self.assertEqual(resumed["stage"], "approval_required")

    def test_interruption_after_key_before_metadata_retains_and_recovers_key(self):
        original = Output.json
        def interrupted(output, name, value):
            if name == "setup.public.json":
                raise KeyboardInterrupt()
            return original(output, name, value)
        with patch.object(Output, "json", interrupted):
            first, code = self.run_onboard()
        self.assertEqual((first["stage"], code), ("interrupted", 130))
        retained = self.key_bytes()
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Do not replace key")):
            resumed, code = self.run_onboard()
        self.assertEqual((resumed["stage"], code), ("approval_required", 2))
        self.assertEqual(self.key_bytes(), retained)

    def test_legacy_metadata_without_key_does_not_generate_replacement(self):
        self.directory.mkdir(mode=0o700)
        old = {"schema": "2n20-local-setup-v1", "vaultLink": LINK, "tradingKey": Account.create().address}
        path = self.directory / "setup.public.json"
        path.write_text(json.dumps(old)); path.chmod(0o600)
        original = path.read_bytes()
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Missing recorded key")):
            resumed, code = self.run_onboard()
        self.assertEqual((resumed["errorCode"], code), ("KEY_MISSING", 4))
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.directory / "setup.recovered.public.json").exists())
        self.assertFalse((self.directory / "strategy.key").exists())
        self.assertFalse(self.requests)

    def test_checkpoint_failure_after_key_write_preserves_and_resumes(self):
        original = Output.atomic_json
        def failed(output, name, value):
            if value.get("tradingKey"):
                raise OSError("Disposable persistence failure")
            return original(output, name, value)
        with patch.object(Output, "atomic_json", failed):
            _, code = self.run_onboard()
        self.assertEqual(code, 3)
        retained = self.key_bytes()
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("No second key")):
            _, code = self.run_onboard()
        self.assertEqual(code, 2)
        self.assertEqual(self.key_bytes(), retained)

    def test_conflicting_directory_vault_or_nomination_never_replaces_key(self):
        self.run_onboard(); retained = self.key_bytes()
        wrong, code = onboard(LINK.replace("a" * 40, "e" * 40), self.directory)
        self.assertEqual(code, 4)
        self.rpc.key = [int("0x" + "d" * 40, 16), 0, 1]
        conflict, code = self.run_onboard()
        self.assertEqual((conflict["stage"], code), ("conflict", 4))
        self.assertEqual(retained, self.key_bytes())
        self.assertEqual(len(self.requests), 1)

    def test_untrusted_service_bindings_and_url_only_allow_public_file_fallback(self):
        real = self.service
        def forged(url, payload, **options):
            return real(url, payload) | {"approvalUrl": "https://attacker.invalid/approve"}
        with patch("twon20.onboarding.request_json", side_effect=forged):
            value, code = self.run_onboard()
        self.assertEqual(code, 2)
        self.assertTrue(value["handoffUnavailable"])
        self.assertNotIn("approvalUrl", value)

    def test_nomination_and_unavailable_core_evidence_remain_pending(self):
        self.run_onboard()
        key = self.requests[0]["consent"]["key"]
        self.rpc.key = [int(key, 16), 0, 1]
        self.rpc.key_status = [int(key, 16), self.rpc.now + 3600, int(OWNER, 16), 0]
        with patch("twon20.status.request_json", side_effect=SetupError("PUBLIC_READ_UNAVAILABLE", "Offline fixture")):
            value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("approval_pending", 2))

    def test_json_cli_errors_and_results_do_not_print_private_material(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["onboard", "--vault", LINK, "--directory", str(self.directory), "--json"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(args), 2)
        key = self.key_bytes().decode().strip()
        self.assertNotIn(key, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["stage"], "approval_required")

    def test_gitignore_is_updated_before_private_creation_and_preserves_patterns(self):
        subprocess.run(["git", "init", "-q", str(self.cwd)], check=True)
        ignore = self.cwd / ".gitignore"; ignore.write_text("# retained\nnode_modules/\n")
        self.directory = self.cwd / "setup[one]"
        _, code = self.run_onboard()
        self.assertEqual(code, 2)
        self.assertTrue(ignore.read_text().startswith("# retained\nnode_modules/\n"))
        checked = subprocess.run(["git", "-C", str(self.cwd), "check-ignore", "--quiet", "--", str(self.directory / "strategy.key")])
        self.assertEqual(checked.returncode, 0)

    def test_bounded_polling_uses_backoff_and_preserves_handoff(self):
        clock = [0.0]; delays = []
        def sleep(seconds):
            delays.append(seconds); clock[0] += seconds
        scoped_time = SimpleNamespace(time=time.time, monotonic=lambda: clock[0], sleep=sleep)
        with patch("twon20.onboarding.time", scoped_time):
            value, code = self.run_onboard(wait=15)
        self.assertEqual((value["stage"], code), ("approval_required", 2))
        self.assertEqual(delays, [2, 4, 8, 1])
        self.assertEqual(len(self.requests), 1)

    def test_interruption_before_key_write_recovers_reservation_and_creates_one_key(self):
        original = Output.write
        def interrupted(output, name, text):
            if name == "strategy.key":
                raise KeyboardInterrupt()
            return original(output, name, text)
        with patch.object(Output, "write", interrupted):
            value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("interrupted", 130))
        self.assertFalse((self.directory / "strategy.key").exists())
        self.assertTrue((self.directory / "key-reservation.public.json").exists())
        self.assertEqual(self.run_onboard()[1], 2)
        retained = self.key_bytes()
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("No duplicate")):
            self.assertEqual(self.run_onboard()[1], 2)
        self.assertEqual(retained, self.key_bytes())

    def test_key_metadata_mismatch_is_not_silently_recovered(self):
        self.run_onboard()
        retained = self.key_bytes()
        metadata_path = self.directory / "setup.public.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["tradingKey"] = "0x" + "d" * 40
        metadata_path.write_text(json.dumps(metadata))
        value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("conflict", 4))
        self.assertEqual(value["errorCode"], "KEY_RECORD_MISMATCH")
        self.assertFalse((self.directory / "setup.recovered.public.json").exists())
        self.assertEqual(retained, self.key_bytes())

    def test_missing_retained_key_and_unsafe_checkpoint_do_not_create_replacement(self):
        self.run_onboard()
        retained = self.key_bytes()
        key_path = self.directory / "strategy.key"
        key_path.unlink()
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Missing retained key")):
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], code), ("KEY_MISSING", 4))
        key_path.write_bytes(retained); key_path.chmod(0o600)
        checkpoint_path = self.directory / CHECKPOINT
        checkpoint_path.chmod(0o644)
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Unsafe checkpoint")):
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], code), ("UNSAFE_FILE_PERMISSIONS", 4))
        self.assertEqual(retained, self.key_bytes())

    def test_atomic_public_checkpoint_failure_keeps_previous_bytes(self):
        self.run_onboard()
        output = open_output(self.directory)
        previous = (self.directory / CHECKPOINT).read_bytes()
        try:
            with patch("twon20.files.os.replace", side_effect=OSError("Disposable rename failure")), self.assertRaises(OSError):
                output.atomic_json(CHECKPOINT, {"stage": "changed"})
        finally:
            output.close()
        self.assertEqual((self.directory / CHECKPOINT).read_bytes(), previous)
        self.assertFalse(list(self.directory.glob("*.tmp")))

    def test_concurrent_command_returns_conflict_without_second_key_or_handoff(self):
        import fcntl
        self.run_onboard()
        retained = self.key_bytes()
        with (self.directory / "onboarding.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], code), ("ONBOARDING_BUSY", 4))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(retained, self.key_bytes())

    def test_response_persistence_failure_recovers_same_service_request(self):
        original = Output.atomic_json
        def failed(output, name, value):
            if value.get("handoff"):
                raise OSError("Lost checkpoint after public service accepted")
            return original(output, name, value)
        with patch.object(Output, "atomic_json", failed):
            value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("retry_required", 3))
        retained = self.key_bytes()
        value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("approval_required", 2))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertEqual(len(self.service_receipts), 1)
        self.assertEqual(retained, self.key_bytes())

    def test_stale_consent_requires_explicit_renew_then_adopts_same_key(self):
        self.run_onboard(); retained = self.key_bytes()
        old_public = (self.directory / "consent.public.json").read_bytes()
        self.rpc.key[2] = 1
        value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("consent_expired", 2))
        with patch("twon20.cli.discover", side_effect=self.discover), patch("twon20.cli.Rpc", return_value=self.rpc), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["renew", "--directory", str(self.directory), "--json"]), 0)
        value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("approval_required", 2))
        self.assertEqual(self.requests[-1]["consent"]["nonce"], "2")
        self.assertEqual(self.requests[0]["consent"]["key"], self.requests[-1]["consent"]["key"])
        self.assertEqual(retained, self.key_bytes())
        self.assertEqual(old_public, (self.directory / "consent.public.json").read_bytes())

    def legacy_long_consent(self):
        found = self.discover(LINK)
        output = new_output(found, self.directory)
        try:
            wallet = retain_key(output, found)
            value = prepare(found, wallet, self.rpc)
            value["deadline"] = str(self.rpc.now + 86400)
            signature = wallet.sign_message(encode_typed_data(full_message=typed_data(found, wallet.address, found.nonce + 1, int(value["deadline"]))))
            value["signature"] = "0x" + signature.signature.hex().removeprefix("0x")
            output.json("consent.public.json", value)
        finally:
            output.close()

    def test_expired_link_renews_still_valid_legacy_consent_with_same_key(self):
        self.legacy_long_consent()
        first, code = self.run_onboard()
        self.assertEqual(code, 2)
        retained = self.key_bytes()
        old_public = (self.directory / "consent.public.json").read_bytes()
        self.rpc.now += 3601
        scoped_time = SimpleNamespace(time=lambda: self.rpc.now, monotonic=time.monotonic, sleep=time.sleep)
        with contextlib.ExitStack() as clock_patches:
            for module in ("onboarding", "consent", "discovery", "status"):
                clock_patches.enter_context(patch(f"twon20.{module}.time", scoped_time))
            clock_patches.enter_context(patch("twon20.onboarding.Account.create", side_effect=AssertionError("Same retained key")))
            renewed, code = self.run_onboard()
            repeated, repeat_code = self.run_onboard()
        self.assertEqual((renewed["stage"], code, repeat_code), ("approval_required", 2, 2))
        self.assertNotEqual(first["requestId"], renewed["requestId"])
        self.assertEqual(renewed["requestId"], repeated["requestId"])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0]["consent"]["key"], self.requests[-1]["consent"]["key"])
        self.assertEqual(retained, self.key_bytes())
        self.assertEqual(old_public, (self.directory / "consent.public.json").read_bytes())

    def test_service_expired_response_renews_eligible_unused_consent_once(self):
        self.legacy_long_consent()
        original = self.service
        failures = [True]
        def expired(url, payload, **options):
            if failures.pop() if failures else False:
                raise SetupError("HANDOFF_EXPIRED", "Expired response fixture")
            return original(url, payload, **options)
        with patch("twon20.onboarding.request_json", side_effect=expired):
            value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("approval_required", 2))
        self.assertNotEqual(Path(value["publicConsentFile"]).name, "consent.public.json")
        self.assertEqual(len(self.requests), 1)

    def test_tracked_private_destination_blocks_creation_before_wallet_generation(self):
        subprocess.run(["git", "init", "-q", str(self.cwd)], check=True)
        self.directory.mkdir(mode=0o700)
        key_path = self.directory / "strategy.key"
        key_path.write_text("disposable tracked placeholder, not a key\n")
        subprocess.run(["git", "-C", str(self.cwd), "add", "--", "setup/strategy.key"], check=True)
        key_path.unlink()
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Tracked path")):
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], code), ("TRACKED_KEY_PATH", 4))
        self.assertFalse(key_path.exists())
        self.assertFalse(self.requests)

    def test_invalid_poll_budget_has_stable_json_conflict(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["onboard", "--vault", LINK, "--wait", "301", "--json"])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(stdout.getvalue())["errorCode"], "INVALID_WAIT")
        self.assertFalse(self.directory.exists())

    def test_first_setup_with_existing_nomination_or_revocation_never_generates_key(self):
        self.rpc.key = [int("0x" + "d" * 40, 16), 0, 1]
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Existing nomination")):
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], code), ("KEY_CONFLICT", 4))
        self.assertFalse((self.directory / "strategy.key").exists())
        self.rpc.key = [0, 1, 0]
        with patch("twon20.onboarding.Account.create", side_effect=AssertionError("Pending revocation")):
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], code), ("REVOCATION_PENDING", 2))
        self.assertFalse((self.directory / "strategy.key").exists())
        self.assertFalse(self.requests)

    def test_service_key_requested_race_requires_authoritative_matching_state(self):
        def nominated(url, payload, **options):
            self.rpc.key = [int(payload["consent"]["key"], 16), 0, 1]
            raise SetupError("KEY_REQUESTED", "Fixture human submitted nomination")
        with patch("twon20.onboarding.request_json", side_effect=nominated):
            value, code = self.run_onboard()
        self.assertEqual((value["stage"], code), ("approval_pending", 2))
        self.assertFalse(value["publicEvidence"]["tradingAccessReady"])
        self.assertIsNone(value["requiredHumanAction"])
        self.assertNotIn("approvalUrl", value)

    def test_fatal_service_signature_error_is_conflict_not_import_fallback(self):
        with patch("twon20.onboarding.request_json", side_effect=SetupError("INVALID_SIGNATURE", "Fixture signature rejected")):
            value, code = self.run_onboard()
        self.assertEqual((value["errorCode"], value["stage"], code), ("INVALID_SIGNATURE", "conflict", 4))
        self.assertNotIn("handoffUnavailable", value)
        self.assertTrue(value["publicArtifacts"]["consent"].endswith("consent.public.json"))


class SkillExportTests(unittest.TestCase):
    def test_explicit_export_writes_only_bundled_skill_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            destination = parent / "chosen-name"
            value = export_skill(destination)
            contents = (destination / "SKILL.md").read_text()
            self.assertIn("name: 2n20-setup", contents)
            self.assertIn("2n20 onboard --vault", contents)
            self.assertEqual(sorted(path.name for path in destination.iterdir()), ["SKILL.md"])
            self.assertEqual(value["skillFile"], str(destination / "SKILL.md"))
            with self.assertRaises(SetupError) as caught:
                export_skill(destination)
            self.assertEqual(caught.exception.code, "SKILL_DIRECTORY_EXISTS")
            self.assertEqual(contents, (destination / "SKILL.md").read_text())
            self.assertFalse((parent / ".agents").exists())
            linked = parent / "linked"
            linked.symlink_to(destination, target_is_directory=True)
            with self.assertRaises(OSError):
                export_skill(linked / "nested")

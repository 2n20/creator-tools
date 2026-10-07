import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eth_account import Account
from eth_account.messages import encode_typed_data

from twon20.completion import ENDPOINT, complete_setup, typed_data
from twon20.discovery import discover
from twon20.errors import SetupError
from twon20.files import new_output, retain_key
from twon20.rpc import TransientReadError
from support import LINK, FakeRpc


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.rpc = FakeRpc()
        found = discover(LINK, self.rpc, self.rpc.now, self.rpc.anchor)
        output = new_output(found, Path(self.temp.name).resolve() / 'setup')
        self.directory = output.path
        self.wallet = retain_key(output, found)
        output.close()
        self.rpc.key = [int(self.wallet.address, 16), 0, 9007199254740993]
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('twon20.completion.discover', side_effect=lambda link: discover(link, self.rpc, self.rpc.now, self.rpc.anchor)))
        self.ready = self.stack.enter_context(patch('twon20.completion.status', return_value={'tradingAccessReady': True}))
        self.requests = []

    def service(self, endpoint, report):
        self.assertEqual(endpoint, ENDPOINT)
        self.assertEqual(Account.recover_message(encode_typed_data(full_message=typed_data(report)), signature=report['signature']).lower(), self.wallet.address.lower())
        self.requests.append(report)
        return {'completion': {name: report[name] for name in ('deploymentId', 'vaultAddress', 'ownerAddress', 'coreAccount', 'tradingKey', 'keyNonce')} | {'completedAt': '2026-10-07T00:00:00.000Z'}}

    def complete(self, **kwargs):
        return complete_setup(LINK, self.directory, True, True, submit=kwargs.get('submit', self.service), now=self.rpc.now)

    def test_signed_report_matches_retained_key_and_preserves_directory(self):
        result = self.complete()
        self.assertTrue(result['setupComplete'])
        self.assertEqual(result['completion']['keyNonce'], '9007199254740993')
        self.assertTrue((self.directory / 'setup-report.public.json').is_file())
        again = self.complete()
        self.assertEqual(again, result)
        self.assertEqual(len(self.requests), 2)
        self.assertNotIn('privateKey', self.requests[0])

    def test_failed_checks_or_changed_key_never_sign_or_report(self):
        with self.assertRaisesRegex(SetupError, 'Run the project'):
            complete_setup(LINK, self.directory, False, True)
        self.ready.return_value = {'tradingAccessReady': False}
        with patch('twon20.completion.load_key') as private:
            with self.assertRaises(SetupError):
                self.complete()
            private.assert_not_called()
            self.rpc.key[0] = int('e' * 40, 16)
            with self.assertRaises(SetupError):
                self.complete()
            private.assert_not_called()
        self.assertEqual(self.requests, [])

    def test_lost_response_retries_identical_report_and_rejects_wrong_acknowledgement(self):
        def ambiguous(endpoint, report):
            accepted = self.service(endpoint, report)
            if len(self.requests) == 1:
                raise TransientReadError()
            return accepted
        with patch('twon20.completion.time.sleep'):
            self.assertTrue(self.complete(submit=ambiguous)['setupComplete'])
        self.assertEqual(self.requests[0], self.requests[1])
        with self.assertRaisesRegex(SetupError, 'did not acknowledge'):
            self.complete(submit=lambda endpoint, report: {'completion': {'tradingKey': '0x' + 'f' * 40}})

    def test_failure_backoff_is_bounded_and_preserves_existing_key(self):
        with patch('twon20.completion.load_key', return_value=self.wallet), patch('twon20.completion.time.sleep') as sleep:
            with self.assertRaisesRegex(SetupError, 'reporting is unavailable'):
                self.complete(submit=lambda endpoint, report: (_ for _ in ()).throw(TransientReadError(retry_after=600)))
            sleep.assert_not_called()
        self.assertTrue((self.directory / 'strategy.key').is_file())

    def test_public_interoperability_vector_has_separate_software_domain(self):
        vector = json.loads((Path(__file__).parent / 'fixtures/software-setup.public.json').read_text())
        recovered = Account.recover_message(encode_typed_data(full_message=typed_data(vector['report'])), signature=vector['report']['signature'])
        self.assertEqual(recovered.lower(), vector['report']['tradingKey'])
        self.assertEqual(typed_data(vector['report'])['domain']['name'], '2n20 Software Setup')

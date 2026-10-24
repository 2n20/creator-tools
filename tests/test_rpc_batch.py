"""Fixed-block batching preserves response identity and fail-closed evidence."""
import copy
import unittest
from unittest.mock import patch
from twon20.errors import SetupError
from twon20.rpc import Rpc, call_data


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.fixed={"blockHash":"0x"+"a"*64,"requireCanonical":True}
        self.target="0x"+"1"*40
        self.reads=[("eth_getCode",[self.target,self.fixed]),
                    ("eth_call",[{"to":self.target,"data":call_data("owner()",[])},self.fixed])]

    def replies(self,payload):
        return [{"jsonrpc":"2.0","id":row["id"],"result":"0x"+str(index+1)*64}
                for index,row in enumerate(payload)]

    def test_unordered_batch_is_bound_to_exact_parameters_and_fixed_block(self):
        rpc=Rpc("mainnet")
        def respond(url,payload,**kwargs): return list(reversed(self.replies(payload)))
        with patch("twon20.rpc.request_json",side_effect=respond) as send:
            rpc.prefetch(self.reads)
            self.assertEqual(rpc.call(*self.reads[0]),"0x"+"1"*64)
            self.assertEqual(rpc.words(self.target,"owner()",[],self.fixed,1),[int("2"*64,16)])
            rpc.prefetch(self.reads)
            self.assertEqual(send.call_count,1)
            changed={**self.fixed,"blockHash":"0x"+"b"*64}
            rpc.prefetch_words([(self.target,"owner()",[])],changed)
            self.assertEqual(send.call_count,2)
            self.assertEqual(send.call_args.args[1][0]["params"][1],changed)
        self.assertEqual(Rpc("mainnet")._prefetched,{})

    def test_missing_duplicate_foreign_boolean_ids_and_mixed_error_never_cache(self):
        good=self.replies([{"id":1},{"id":2}])
        cases=[good[:1],[good[0],good[0]],[good[0],{**good[1],"id":3}],
               [good[0],{**good[1],"id":True}],[good[0],{**good[1],"jsonrpc":"1.0"}],
               [good[0],{**good[1],"error":{"code":3,"message":"PRIVATE_PROVIDER_TEXT"}}],{},None]
        for reply in cases:
            rpc=Rpc("mainnet")
            with self.subTest(reply=reply),patch("twon20.rpc.request_json",return_value=reply),self.assertRaises(SetupError) as caught:
                rpc.prefetch(self.reads)
            self.assertEqual(rpc._prefetched,{})
            self.assertNotIn("PRIVATE_PROVIDER_TEXT",caught.exception.message)

    def test_transient_batch_retries_same_ids_and_block_with_existing_budget(self):
        reply=self.replies([{"id":1},{"id":2}]);failed=copy.deepcopy(reply)
        failed[1]={"jsonrpc":"2.0","id":2,"error":{"code":-32603}}
        rpc=Rpc("mainnet")
        with patch("twon20.rpc.request_json",side_effect=[failed,reply]) as send,patch("time.sleep"):
            rpc.prefetch(self.reads)
        self.assertEqual(send.call_count,2)
        self.assertEqual(send.call_args_list[0].args,send.call_args_list[1].args)
        self.assertEqual(rpc.call(*self.reads[0]),reply[0]["result"])

    def test_latest_uncanonical_mutating_and_oversized_prefetch_are_refused(self):
        for reads in ([('eth_call',[{},'latest'])],[('eth_getCode',[self.target,{**self.fixed,'requireCanonical':False}])],
                      [('eth_sendRawTransaction',['not-a-transaction',self.fixed])],[],self.reads*25):
            with self.subTest(reads=reads),patch('twon20.rpc.request_json') as send,self.assertRaises(SetupError):
                Rpc('mainnet').prefetch(reads)
            send.assert_not_called()
        rpc=Rpc('mainnet')
        with patch('twon20.rpc.request_json',side_effect=lambda url,payload,**kw:{'jsonrpc':'2.0','id':payload['id'],'result':'latest'}) as send:
            rpc.call('eth_getBlockByNumber',['latest',False]);rpc.call('eth_getBlockByNumber',['latest',False])
            self.assertEqual(send.call_count,2)

    def test_cached_contract_results_still_require_exact_abi_length(self):
        rpc=Rpc('mainnet')
        with patch('twon20.rpc.request_json',side_effect=lambda url,payload,**kw:[{'jsonrpc':'2.0','id':row['id'],'result':'0x12'} for row in payload]):
            rpc.prefetch_words([(self.target,'owner()',[])],self.fixed)
        with self.assertRaises(SetupError):rpc.words(self.target,'owner()',[],self.fixed,1)

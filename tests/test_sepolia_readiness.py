"""Synthetic credentials/isolated RPC only; never use local operator credentials."""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from eth_account import Account
from eth_utils import keccak
from backend.domain import Rejected
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.transaction_safety import (
    AnchorFeePolicy, require_sepolia, load_anchor_artifact, prepare_transaction,
    plan_hash, sign_approved_plan, broadcast_approved,
)

KEY = '0' * 63 + '1'
OWNER = Account.from_key(KEY).address
TARGET = '0x' + '4' * 40
CODE = '0x6000'
CODEHASH = '0x' + keccak(hexstr=CODE).hex()
DEPLOY = '0x' + load_anchor_artifact()['bin']
COMMIT = '0x' + keccak(text='commit(bytes32,bytes32,bytes32,uint64,uint32)')[:4].hex() + '1'*64 + '2'*64 + '3'*64 + f'{1:064x}'*2


class PlanRPC:
    def __init__(self):
        self.chain=11155111;self.base=13;self.tip=1000000;self.gas=3802284
        self.balance=103*10**14;self.nonce=0;self.publisher=OWNER;self.calls=[];self.sent=[]

    def call(self,method,params):
        self.calls.append(method)
        if method=='eth_chainId':return hex(self.chain)
        if method=='eth_getBlockByNumber':return {'number':'0x123','hash':'0x'+'a'*64,'baseFeePerGas':hex(self.base)}
        if method=='eth_maxPriorityFeePerGas':return hex(self.tip)
        if method=='eth_estimateGas':return hex(self.gas)
        if method=='eth_getBalance':return hex(self.balance)
        if method=='eth_getTransactionCount':return hex(self.nonce)
        if method=='eth_getCode':return CODE
        if method=='eth_call':return '0x'+'0'*24+self.publisher[2:].lower()
        if method=='eth_sendRawTransaction':
            self.sent.append(params[0]);return '0x'+keccak(hexstr=params[0]).hex()
        raise AssertionError(method)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.rpc=PlanRPC()
        self.env=patch.dict(os.environ,{'ANCHOR_WALLET_ADDRESS':OWNER,'ANCHOR_CONTRACT_CODE_HASH':CODEHASH,
            'ANCHOR_ALLOW_SIGNING':'false','ANCHOR_ALLOW_BROADCAST':'false','ANCHOR_APPROVED_PLAN_HASH':'',
            'ANCHOR_KEYSTORE_PATH':'','TEST_ANCHOR_KEY':KEY})
        self.env.start();self.addCleanup(self.env.stop)

    def plan(self):return prepare_transaction(self.rpc,OWNER,DEPLOY,'deploy')

    def approve(self,plan):
        os.environ.update(ANCHOR_ALLOW_SIGNING='true',ANCHOR_ALLOW_BROADCAST='true',ANCHOR_APPROVED_PLAN_HASH=plan_hash(plan))

    def assert_rejected(self,code,fn):
        with self.assertRaises(Rejected) as ctx:fn()
        self.assertEqual(ctx.exception.code,code);self.assertEqual(self.rpc.sent,[])

    def test_unsigned_dynamic_quote(self):
        p=self.plan();self.assertEqual(p['estimated_gas'],3802284);self.assertEqual(p['transaction']['gas'],4562741)
        self.assertEqual(p['transaction']['type'],2);self.assertNotIn('gasPrice',p['transaction'])
        self.assertEqual(p['worst_cost_wei'],4562741*1000026);self.assertEqual(self.rpc.sent,[])

    def test_wrong_chain_stops_before_fee_or_wallet(self):
        self.rpc.chain=1;self.assert_rejected('ANCHOR_CHAIN_MISMATCH',self.plan);self.assertEqual(self.rpc.calls,['eth_chainId'])

    def test_unknown_chain_fails_closed(self):
        with patch.object(self.rpc,'call',side_effect=RuntimeError('private-url')):
            self.assert_rejected('ANCHOR_CHAIN_UNVERIFIED',lambda:require_sepolia(self.rpc))

    def test_deploy_gas_cap(self):
        self.rpc.gas=5000000;self.assert_rejected('ANCHOR_GAS_LIMIT',self.plan)

    def test_priority_price_cap(self):
        self.rpc.tip=100000001;self.assert_rejected('ANCHOR_PRIORITY_FEE_LIMIT',self.plan)

    def test_base_fee_cap(self):
        self.rpc.base=2000000000;self.assert_rejected('ANCHOR_FEE_LIMIT',self.plan)

    def test_total_fee_cap(self):
        self.rpc.base=950000000;self.assert_rejected('ANCHOR_TOTAL_FEE_LIMIT',self.plan)

    def test_residual_balance_protected(self):
        self.rpc.balance=200000000000000;self.assert_rejected('ANCHOR_BALANCE_RESERVE',self.plan)

    def test_wrong_deployment_artifact(self):
        self.assert_rejected('ANCHOR_DEPLOYMENT_ARTIFACT_MISMATCH',lambda:prepare_transaction(self.rpc,OWNER,'0x6000','deploy'))

    def test_commit_dynamic_quote_and_publisher(self):
        self.rpc.gas=356357;p=prepare_transaction(self.rpc,OWNER,COMMIT,'commit',TARGET)
        self.assertEqual(p['transaction']['gas'],427629);self.assertEqual(self.rpc.sent,[])

    def test_wrong_publisher(self):
        self.rpc.publisher='0x'+'5'*40
        self.assert_rejected('ANCHOR_PUBLISHER_MISMATCH',lambda:prepare_transaction(self.rpc,OWNER,COMMIT,'commit',TARGET))

    def test_wrong_code_pin(self):
        os.environ['ANCHOR_CONTRACT_CODE_HASH']='0x'+'0'*64
        self.assert_rejected('ANCHOR_CONTRACT_CODE_MISMATCH',lambda:prepare_transaction(self.rpc,OWNER,COMMIT,'commit',TARGET))

    def test_commit_gas_cap(self):
        self.rpc.gas=600000
        self.assert_rejected('ANCHOR_GAS_LIMIT',lambda:prepare_transaction(self.rpc,OWNER,COMMIT,'commit',TARGET))

    def test_wrong_commit_schema(self):
        self.assert_rejected('ANCHOR_COMMIT_CALLDATA_INVALID',lambda:prepare_transaction(self.rpc,OWNER,COMMIT[:-1]+'2','commit',TARGET))

    def test_no_signature_without_exact_approval(self):
        p=self.plan()
        with patch('eth_account.Account.from_key',side_effect=AssertionError('key must not be loaded')):
            self.assert_rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED',lambda:sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY'))

    def test_signing_wrong_chain(self):
        p=self.plan();self.approve(p);self.rpc.chain=1
        self.assert_rejected('ANCHOR_CHAIN_MISMATCH',lambda:sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY'))

    def test_approval_cannot_follow_changed_calldata(self):
        p=self.plan();self.approve(p);p['transaction']['data']='0x6000'
        self.assert_rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED',lambda:sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY'))

    def test_quote_expiry(self):
        p=self.plan();p['quoted_at']=int(time.time())-121;self.approve(p)
        self.assert_rejected('ANCHOR_QUOTE_EXPIRED',lambda:sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY'))

    def test_nonce_change(self):
        p=self.plan();self.approve(p);self.rpc.nonce=1
        self.assert_rejected('ANCHOR_QUOTE_CHANGED',lambda:sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY'))

    def test_signer_mismatch(self):
        p=self.plan();self.approve(p);os.environ['TEST_ANCHOR_KEY']='0'*63+'2'
        self.assert_rejected('ANCHOR_SIGNER_MISMATCH',lambda:sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY'))

    def test_synthetic_eip1559_signature_and_broadcast_binding(self):
        p=self.plan();self.approve(p);signed=sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY')
        self.assertEqual(Account.recover_transaction(signed.raw_transaction),OWNER)
        self.assertEqual(broadcast_approved(self.rpc,p,signed),'0x'+signed.hash.hex().removeprefix('0x'))
        self.assertEqual(len(self.rpc.sent),1)

    def test_broadcast_disabled_after_local_synthetic_sign(self):
        p=self.plan();self.approve(p);signed=sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY');os.environ['ANCHOR_ALLOW_BROADCAST']='false'
        self.assert_rejected('ANCHOR_BROADCAST_APPROVAL_REQUIRED',lambda:broadcast_approved(self.rpc,p,signed))

    def test_broadcast_wrong_chain(self):
        p=self.plan();self.approve(p);signed=sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY');self.rpc.chain=1
        self.assert_rejected('ANCHOR_CHAIN_MISMATCH',lambda:broadcast_approved(self.rpc,p,signed))

    def test_raw_signed_nonce_tampering(self):
        p=self.plan();self.approve(p);other=copy.deepcopy(p['transaction']);other['nonce']=9
        signed=Account.from_key(KEY).sign_transaction(other)
        self.assert_rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH',lambda:broadcast_approved(self.rpc,p,signed))

    def test_encrypted_local_synthetic_keystore(self):
        p=self.plan();self.approve(p)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'test-keystore.json';path.write_text(json.dumps(Account.encrypt(KEY,'synthetic-password',kdf='pbkdf2',iterations=1)))
            os.environ.update(ANCHOR_KEYSTORE_PATH=str(path),ANCHOR_KEYSTORE_PASSWORD='synthetic-password')
            signed=sign_approved_plan(self.rpc,p,'TEST_ANCHOR_KEY')
            self.assertEqual(Account.recover_transaction(signed.raw_transaction),OWNER)

    def test_raw_rpc_broadcast_and_remote_signing_rejected(self):
        rpc=EthereumRPC('https://example.invalid')
        with patch('urllib.request.build_opener',side_effect=AssertionError('must not connect')):
            self.assert_rejected('ANCHOR_BROADCAST_APPROVAL_REQUIRED',lambda:rpc.call('eth_sendRawTransaction',['0x01']))
            self.assert_rejected('ANCHOR_RPC_METHOD_FORBIDDEN',lambda:rpc.call('eth_sign',[]))

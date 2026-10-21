from pathlib import Path
from datetime import datetime,timezone,timedelta
import json
import tempfile
import unittest
from types import SimpleNamespace
from backend.domain import Rejected
from backend.bank_sdk.reconciliation import reconcile
from backend.bank_sdk.contracts import BankError,UncertainBankWrite
from scripts.revolut_sandbox_settlement import advance,save_report
from tests import test_revolut_banking as banking


class SandboxSettlementTests(unittest.TestCase):
    def setUp(self):
        f=banking.TransferTests();f.setUp();self.addCleanup(f.temp.cleanup)
        self.f=f;self.s=f.s;f.net.pending=True
        self.task=f.approve(f.propose());self.directory=Path(f.temp.name)
        self.calls=[];old=f.net.send
        def send(method,path,body=b'',headers=None):
            if path=='/api/1.0/sandbox/transactions/'+banking.TX+'/complete':
                self.calls.append((method,path))
                if getattr(self,'lose_before',False):raise UncertainBankWrite()
                if getattr(self,'deny',False):raise BankError('BANK_SCOPE_DENIED',403)
                f.net.settle()
                if getattr(self,'lose',False):raise UncertainBankWrite()
                return {'id':banking.TX,'state':'completed'}
            return old(method,path,body,headers)
        f.net.send=send;f.provider.auth.access_token=lambda:'synthetic'

    def test_existing_authorised_pending_only_and_original_reconciliation(self):
        result=advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(result['simulation_post_calls'],1);self.assertEqual(len(self.f.net.posts),1)
        done=reconcile(self.s,self.task['task_id'],'demo')
        self.assertEqual(done['reconciliation']['status'],'VERIFIED');self.assertEqual(done['status'],'MANUAL_REVIEW')
        again=advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(again['simulation_post_calls'],0);self.assertEqual(len(self.calls),1)

    def test_lost_simulator_response_never_repeats_or_resends_payment(self):
        self.lose=True
        self.assertEqual(advance(self.s,self.task['task_id'],self.directory)['observed_state'],'SIMULATION_RESPONSE_UNCERTAIN')
        done=reconcile(self.s,self.task['task_id'],'demo')
        self.assertEqual(done['reconciliation']['status'],'VERIFIED')
        advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.f.net.posts),1)

    def test_prior_claim_pending_does_not_call_simulator(self):
        (self.directory/('simulation-'+banking.TX+'.json')).write_text('{}')
        result=advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(result['simulation_post_calls'],0);self.assertEqual(self.calls,[])

    def test_known_bank_rejection_keeps_pending_without_retry_or_receipt(self):
        self.deny=True
        result=advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(result['observed_state'],'SIMULATION_REJECTED')
        self.assertEqual(result['http_status'],403)
        done=reconcile(self.s,self.task['task_id'],'demo')
        self.assertEqual(done['reconciliation']['status'],'MANUAL_REVIEW')
        self.assertFalse(done.get('receipt') or done['reconciliation'].get('receipt'))
        again=advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(again['simulation_post_calls'],0)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.f.net.posts),1)

    def test_mismatch_cannot_be_made_successful_with_simulation(self):
        self.f.net.raw['reference']='wrong'
        with self.assertRaises(Rejected):advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(self.calls,[])

    def test_unapproved_task_and_production_are_denied(self):
        draft=self.f.propose()
        with self.assertRaises(Rejected):advance(self.s,draft['task_id'],self.directory)
        self.f.net.base_url='https://b2b.revolut.com'
        with self.assertRaises(Rejected):advance(self.s,self.task['task_id'],self.directory)
        self.assertEqual(self.calls,[])

    def test_business_sdk_cannot_invoke_simulator(self):
        from backend.bank_sdk.revolut_auth import RevolutTransferAuth
        auth=object.__new__(RevolutTransferAuth)
        with self.assertRaises(Rejected):auth.headers('POST','/api/1.0/sandbox/transactions/'+banking.TX+'/complete')
        self.assertNotIn('sandbox.simulate',self.f.provider.manifest.capabilities)

    def operator_credentials(self,**changes):
        self.f.provider.auth.key_path=str(self.directory/'registered.pem')
        self.f.provider.auth.client_id='synthetic-client'
        row={'client_id':'synthetic-client','issuer':'trusta.localhost','sandbox_only':True,
            'operator_simulation_only':True,'scope_requested':'READ,WRITE','token_type':'bearer',
            'access_token':'synthetic-operator','obtained_at':datetime.now(timezone.utc).isoformat(),'expires_in':2399}
        row.update(changes)
        (self.directory/'oauth-simulation-tokens.local.json').write_text(json.dumps(row))

    def test_operator_trial_binds_audited_rejection_and_preserves_original_claim(self):
        self.deny=True;prior=advance(self.s,self.task['task_id'],self.directory)
        original=(self.directory/('simulation-'+banking.TX+'.json')).read_bytes()
        self.operator_credentials();self.deny=False
        ready=advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior,check_only=True)
        self.assertEqual(ready['simulation_post_calls'],0);self.assertEqual(len(self.calls),1)
        result=advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(result['observed_state'],'SIMULATOR_REPORTED_COMPLETED')
        self.assertEqual((self.directory/('simulation-'+banking.TX+'.json')).read_bytes(),original)
        self.assertEqual(reconcile(self.s,self.task['task_id'],'demo')['reconciliation']['status'],'VERIFIED')
        advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(len(self.calls),2);self.assertEqual(len(self.f.net.posts),1)

    def test_uncertain_simulation_cannot_enable_operator_retry(self):
        self.lose_before=True;prior=advance(self.s,self.task['task_id'],self.directory)
        self.operator_credentials()
        with self.assertRaises(Rejected):
            advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.f.net.posts),1)

    def test_tampered_rejection_or_stale_or_pay_credentials_block_operator_post(self):
        self.deny=True;prior=advance(self.s,self.task['task_id'],self.directory)
        self.operator_credentials()
        for change in ({'http_status':401},{'currency':'EUR'},{'error':'EXTERNAL_WRITE_UNKNOWN'}):
            with self.assertRaises(Rejected):
                advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=dict(prior,**change))
        for change in ({'scope':'READ,WRITE,PAY'},{'operator_simulation_only':False},
                       {'obtained_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}):
            self.operator_credentials(**change)
            with self.assertRaises(Rejected):
                advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(len(self.calls),1)

    def test_second_trial_403_pending_never_retries(self):
        self.deny=True;prior=advance(self.s,self.task['task_id'],self.directory)
        self.operator_credentials()
        result=advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(result['observed_state'],'SIMULATION_REJECTED')
        again=advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(again['simulation_post_calls'],0)
        self.assertEqual(len(self.calls),2);self.assertEqual(len(self.f.net.posts),1)
        self.assertEqual(reconcile(self.s,self.task['task_id'],'demo')['reconciliation']['status'],'MANUAL_REVIEW')

    def test_second_trial_lost_response_pending_never_retries(self):
        self.deny=True;prior=advance(self.s,self.task['task_id'],self.directory)
        self.operator_credentials();self.deny=False;self.lose_before=True
        result=advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(result['observed_state'],'SIMULATION_RESPONSE_UNCERTAIN')
        again=advance(self.s,self.task['task_id'],self.directory,operator_retry=True,rejection_report=prior)
        self.assertEqual(again['simulation_post_calls'],0)
        self.assertEqual(len(self.calls),2);self.assertEqual(len(self.f.net.posts),1)
        self.assertEqual(reconcile(self.s,self.task['task_id'],'demo')['reconciliation']['status'],'MANUAL_REVIEW')

    def test_read_only_report_preserves_historical_bank_attempt(self):
        self.deny=True;first=advance(self.s,self.task['task_id'],self.directory)
        path=self.directory/'validation.json';save_report(path,first)
        again=advance(self.s,self.task['task_id'],self.directory);save_report(path,again)
        saved=json.loads(path.read_text())
        self.assertEqual(saved['http_status'],403);self.assertEqual(saved['simulation_post_calls'],1)
        self.assertEqual(saved['latest_read_only_observation']['simulation_post_calls'],0)
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.f.net.posts),1)


if __name__=='__main__':unittest.main()

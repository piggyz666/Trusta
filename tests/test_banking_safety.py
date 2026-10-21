import base64
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend.config import Config
from backend.domain import Rejected, canonical, digest
from backend.security.banking_controls import hotp
from backend.service import TrustaService


class BankingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config=Config(db_path=str(Path(self.temp.name)/'bank.sqlite3'),signing_key=b'test-only-banking-safety-key-32-bytes',
                           safety_profile='competition',security_operator_token='test-only-operator')
        self.s=TrustaService(self.config)

    def action(self, amount=600):
        task=self.s.create_task(f'给张三转 {amount} 元')
        self.assertEqual(task['status'],'WAITING_USER')
        return task,task['proposed_actions'][0]

    def enroll(self, stamp=1800000000):
        with patch('backend.security.banking_controls.time.time',return_value=stamp):
            result=self.s.safety.begin_enrollment('demo')
            key=base64.b32decode(result['secret']+'='*((-len(result['secret']))%8))
            self.s.safety.confirm_enrollment('demo',hotp(key,stamp//30))
        return key

    def test_rfc4226_and_6238_known_vectors(self):
        key=b'12345678901234567890'
        expected=['755224','287082','359152','969429','338314','254676','287922','162583','399871','520489']
        self.assertEqual([hotp(key,n) for n in range(10)],expected)
        for stamp,value in [(59,'94287082'),(1111111109,'07081804'),(1111111111,'14050471'),
                            (1234567890,'89005924'),(2000000000,'69279037'),(20000000000,'65353130')]:
            self.assertEqual(hotp(key,stamp//30,8),value)

    def test_daily_exact_boundary_and_multiple_accounts_share_customer_quota(self):
        task,a=self.action(1000)
        self.assertEqual(task['policy_decision']['decision'],'CONFIRM')
        self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        task,b=self.action(1)
        self.assertEqual(task['policy_decision']['decision'],'STRONG_AUTH')
        self.assertEqual(task['authorization']['auth_method'],'totp')
        b['source_account_id']='a-different-account'
        self.assertTrue(self.s.safety.risk(b)['requires_strong_auth'])

    def test_daily_quota_is_persistent_and_idempotency_conflict_is_rejected(self):
        _,a=self.action()
        self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        self.assertEqual(len(self.s.db.rows('SELECT * FROM banking_daily_claims')),1)
        self.s=TrustaService(self.config)
        _,b=self.action()
        self.assertTrue(self.s.safety.risk(b)['requires_strong_auth'])
        changed=copy.deepcopy(a);changed['recipient_id']='different'
        with self.assertRaisesRegex(Rejected,'SECURITY_CLAIM_CONFLICT'):
            self.s.safety.before_execution(changed,{'auth_method':'explicit_confirmation'})

    def test_atomic_reservation_stops_concurrent_split_without_strong_auth(self):
        _,a=self.action();_,b=self.action()
        def reserve(action):
            try:
                self.s.safety.before_execution(action,{'auth_method':'explicit_confirmation'})
                return 'RESERVED'
            except Rejected as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(reserve,[a,b]))
        self.assertCountEqual(results,['RESERVED','DAILY_STRONG_AUTH_REQUIRED'])

    def test_daily_upgrade_happens_before_approval_and_write(self):
        task,a=self.action()
        done=self.s.authorize(task['task_id'],'demo',digest(a),True)
        self.assertEqual(done['status'],'COMPLETED')
        second,b=self.action()
        self.assertEqual(second['authorization']['status'],'PENDING_STRONG_AUTH')
        with self.assertRaisesRegex(Rejected,'TOTP_NOT_ENROLLED'):
            self.s.authorize(second['task_id'],'demo',digest(b),True,'test-secret')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_preexisting_local_transfers_are_included_after_upgrade(self):
        old_config=copy.copy(self.config);old_config.db_path=str(Path(self.temp.name)/'old.sqlite3');old_config.safety_profile='legacy'
        old=TrustaService(old_config)
        t=old.create_task('给张三转 600 元');a=t['proposed_actions'][0]
        self.assertEqual(old.authorize(t['task_id'],'demo',digest(a),True)['status'],'COMPLETED')
        old_config.safety_profile='competition';upgraded=TrustaService(old_config)
        t=upgraded.create_task('给张三转 600 元')
        self.assertEqual(t['policy_decision']['decision'],'STRONG_AUTH')

    def test_enrollment_is_encrypted_time_bounded_and_cannot_be_replaced(self):
        with patch('backend.security.banking_controls.time.time',return_value=1800000000):
            result=self.s.safety.begin_enrollment('demo')
            stored=self.s.db.one('SELECT body FROM banking_totp')['body']
            self.assertNotIn(result['secret'],stored)
            with self.assertRaisesRegex(Rejected,'TOTP_ENROLLMENT_PENDING'):
                self.s.safety.begin_enrollment('demo')
        with patch('backend.security.banking_controls.time.time',return_value=1800000301):
            with self.assertRaisesRegex(Rejected,'TOTP_NOT_ENROLLED'):
                self.s.safety.confirm_enrollment('demo','123456')
        self.assertFalse(self.s.safety.status('demo')['totp_enrolled'])

    def test_totp_authorization_proof_is_bound_to_exact_action_and_replay_denied(self):
        now=int(time.time());stamp=now//30*30+30
        key=self.enroll(stamp-30)
        t,a=self.action(2000)
        with patch('backend.security.banking_controls.time.time',return_value=stamp):
            result=self.s.authorize(t['task_id'],'demo',digest(a),True,hotp(key,stamp//30))
        self.assertEqual(result['status'],'COMPLETED')
        self.assertEqual(result['authorization']['auth_method'],'totp')
        _,b=self.action(2000)
        auth=self.s.auth.request(b,self.s.policy.evaluate(b))
        with patch('backend.security.banking_controls.time.time',return_value=stamp):
            with self.assertRaisesRegex(Rejected,'TOTP_REPLAY'):
                self.s.auth.decide(auth['authorization_id'],'demo',b,True,hotp(key,stamp//30))
        changed=copy.deepcopy(result['authorization']);changed['action_hash']=digest(b)
        self.assertFalse(self.s.auth.strong_valid(changed))
        self.assertTrue(self.s.audit.verify_chain())

    def test_wrong_user_or_action_cannot_consume_totp(self):
        now=int(time.time())//30*30+30;key=self.enroll(now-30)
        t,a=self.action(2000)
        with self.assertRaisesRegex(Rejected,'AUTHORIZATION_HASH_MISMATCH'):
            self.s.auth.decide(t['authorization']['authorization_id'],'intruder',a,True,hotp(key,now//30))
        with patch('backend.security.banking_controls.time.time',return_value=now):
            self.assertEqual(self.s.authorize(t['task_id'],'demo',digest(a),True,hotp(key,now//30))['status'],'COMPLETED')

    def test_failures_commit_before_rejection_and_lock_survives_restart(self):
        stamp=int(time.time())//30*30;self.enroll(stamp-30)
        t,a=self.action(2000)
        for _ in range(5):
            with self.assertRaises(Rejected):
                self.s.auth.decide(t['authorization']['authorization_id'],'demo',a,True,'bad-code')
        self.s=TrustaService(self.config)
        self.assertTrue(self.s.safety.status('demo')['locked'])
        with self.assertRaisesRegex(Rejected,'BANKING_SECURITY_LOCKED'):
            self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        # Read-only account access stays available for diagnosis.
        self.assertEqual(self.s.gateway.read('finance','demo','account.get',account_id='acct-demo')['balance'],1000000)
        with self.assertRaisesRegex(Rejected,'SECURITY_OPERATOR_REQUIRED'):
            self.s.safety.unlock('demo','wrong','reviewed the authentication failures')
        self.assertFalse(self.s.safety.unlock('demo','test-only-operator','reviewed the authentication failures')['locked'])

    def test_execution_failures_lock_and_do_not_release_uncertain_quota(self):
        _,a=self.action()
        self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        for _ in range(3): self.s.safety.execution_failed('demo','BANK_WRITE_UNCERTAIN')
        self.assertTrue(self.s.safety.status('demo')['locked'])
        self.assertEqual(len(self.s.db.rows('SELECT * FROM banking_daily_claims')),1)

    def test_success_resets_execution_counter_but_does_not_unlock_existing_lock(self):
        self.s.safety.execution_failed('demo','BANK_WRITE_UNCERTAIN')
        self.s.safety.execution_succeeded('demo')
        self.assertEqual(self.s.safety.status('demo')['execution_failures'],0)
        for _ in range(3): self.s.safety.execution_failed('demo','BANK_WRITE_UNCERTAIN')
        self.s.safety.execution_succeeded('demo')
        self.assertTrue(self.s.safety.status('demo')['locked'])

    def test_gateway_checks_lock_before_calling_bank(self):
        task,a=self.action(100)
        auth=self.s.auth.decide(task['authorization']['authorization_id'],'demo',a,True)
        contract=self.s.contracts.issue(a,task['policy_decision']['policy_decision_id'],auth['authorization_id'])
        for _ in range(3): self.s.safety.execution_failed('demo','BANK_WRITE_UNCERTAIN')
        with patch.object(self.s.gateway._adapter,'create_transfer') as bank:
            with self.assertRaisesRegex(Rejected,'BANKING_SECURITY_LOCKED'):
                self.s.gateway.execute('executor',a,contract)
            bank.assert_not_called()

    def test_tampered_totp_proof_is_rejected_by_contract(self):
        stamp=int(time.time())//30*30+30;key=self.enroll(stamp-30)
        task,a=self.action(2000)
        with patch('backend.security.banking_controls.time.time',return_value=stamp):
            auth=self.s.auth.decide(task['authorization']['authorization_id'],'demo',a,True,hotp(key,stamp//30))
            proof=json.loads(auth['strong_auth_proof']);proof['body']['action_hash']='forged'
            auth['strong_auth_proof']=canonical(proof)
            with self.s.db.transaction() as db:
                db.execute('UPDATE authorizations SET body=? WHERE id=?',(canonical(auth),auth['authorization_id']))
            with self.assertRaisesRegex(Rejected,'SECURITY_STATE_INVALID'):
                self.s.contracts.issue(a,task['policy_decision']['policy_decision_id'],auth['authorization_id'])

    def test_new_six_agent_runtime_uses_totp_for_investment(self):
        cfg=copy.copy(self.config);cfg.banking_version=5;cfg.communication_version=3
        cfg.db_path=str(Path(self.temp.name)/'six-agent.sqlite3')
        service=TrustaService(cfg)
        task=service.create_task('购买sandbox-r1 100元')
        self.assertEqual(task['status'],'WAITING_USER')
        self.assertEqual(task['action_records'][0]['authorization']['auth_method'],'totp')

    def test_http_security_routes_require_owner_authentication_and_operator_for_unlock(self):
        import threading
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from backend.server import create_server
        server=create_server(self.s,'owner-test-token',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():
            server.shutdown();server.server_close();thread.join(5)
        self.addCleanup(close)
        def call(path,body=None,token='owner-test-token',operator=None):
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
            if operator: headers['X-TRUSTA-Operator']=operator
            request=Request(f'http://127.0.0.1:{server.server_port}'+path,
                            data=None if body is None else canonical(body).encode(),headers=headers)
            try:
                with urlopen(request,timeout=10) as response:return response.status,json.load(response)
            except HTTPError as exc:return exc.code,json.load(exc)
        self.assertEqual(call('/api/security/status',token='wrong')[0],403)
        self.assertEqual(call('/api/security/status')[1]['profile'],'competition')
        status,enrollment=call('/api/security/totp/enroll',{})
        self.assertEqual(status,201)
        key=base64.b32decode(enrollment['secret']+'='*((-len(enrollment['secret']))%8))
        self.assertEqual(call('/api/security/totp/confirm',{'code':hotp(key,int(time.time())//30)})[0],200)
        self.assertNotIn(enrollment['secret'],canonical(call('/api/security/status')[1]))
        self.assertEqual(call('/api/security/unlock',{'reason':'Reviewed login failures'})[1]['error'],'SECURITY_OPERATOR_REQUIRED')
        self.assertEqual(call('/api/security/unlock',{'reason':'Reviewed login failures'},operator='test-only-operator')[0],200)

    def test_competition_database_cannot_silently_downgrade(self):
        downgraded=copy.copy(self.config);downgraded.safety_profile='legacy'
        with self.assertRaisesRegex(Rejected,'SAFETY_DOWNGRADE_DENIED'):
            TrustaService(downgraded)

    def test_tampered_security_state_and_proof_are_rejected(self):
        self.s.safety.execution_failed('demo','BANK_WRITE_UNCERTAIN')
        with self.s.db.transaction() as db:
            body=json.loads(db.execute('SELECT body FROM banking_security_users').fetchone()['body'])
            body['body']['execution_failures']=0
            db.execute('UPDATE banking_security_users SET body=?',(canonical(body),))
        with self.assertRaisesRegex(Rejected,'SECURITY_STATE_INVALID'):
            self.s.safety.status('demo')

    def test_currency_buckets_do_not_silently_convert_foreign_money(self):
        _,a=self.action(600);foreign=copy.deepcopy(a);foreign['currency']='EUR'
        self.assertTrue(self.s.safety.risk(foreign)['requires_strong_auth'])
        self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        self.assertEqual(self.s.safety.risk(foreign)['prior_amount_minor'],0)

    def test_day_rollover_uses_shanghai_midnight(self):
        _,a=self.action()
        # 2026-10-08 23:59:59 and 2026-10-09 00:00:01, UTC+8.
        from datetime import datetime,timezone,timedelta
        stamp=datetime(2026,10,8,23,59,59,tzinfo=timezone(timedelta(hours=8))).timestamp()
        with patch('backend.security.banking_controls.time.time',return_value=stamp):
            self.s.safety.before_execution(a,{'auth_method':'explicit_confirmation'})
        _,b=self.action()
        with patch('backend.security.banking_controls.time.time',return_value=stamp+2):
            self.assertEqual(self.s.safety.risk(b)['prior_amount_minor'],0)

    def test_review_notes_cannot_create_receipt_change_authorization_or_bank_state(self):
        task,a=self.action()
        self.s.blackboard.update(task['task_id'],'runtime',status='MANUAL_REVIEW')
        task=self.s.get_task(task['task_id'],'demo')
        case=self.s.safety.review(task,'demo','Bank result needs independent verification')
        with self.assertRaisesRegex(Rejected,'SECURITY_OPERATOR_REQUIRED'):
            self.s.safety.annotate_review(task['task_id'],'demo','wrong','Checked the existing transaction')
        self.s.safety.annotate_review(task['task_id'],'demo','test-only-operator','Checked the existing transaction')
        self.assertEqual(self.s.get_task(task['task_id'],'demo')['status'],'MANUAL_REVIEW')
        self.assertFalse(self.s.db.rows('SELECT * FROM transactions'))
        self.assertFalse(self.s.db.rows('SELECT * FROM receipts'))
        self.assertEqual(self.s.auth.get(task['authorization']['authorization_id'])['status'],'PENDING_CONFIRMATION')
        self.assertEqual(self.s.safety.cases('intruder'),[])
        self.assertEqual(case['case_id'],self.s.safety.review(task,'demo','Same case should not be recreated')['case_id'])

    def test_authoritative_mismatch_locks_but_uncertainty_and_anomaly_do_not(self):
        self.s.safety.verification_result('demo',{'status':'MANUAL_REVIEW','reason':'EXTERNAL_STATUS_UNCERTAIN'})
        self.s.safety.verification_result('demo',{'status':'MANUAL_REVIEW','reason':'POSSIBLE_DUPLICATE'})
        self.assertFalse(self.s.safety.status('demo')['locked'])
        task,a=self.action()
        with patch.object(self.s.verifier,'verify',return_value={'status':'MANUAL_REVIEW','reason':'AUTHORITATIVE_STATE_MISMATCH'}):
            done=self.s.authorize(task['task_id'],'demo',digest(a),True)
        self.assertEqual(done['status'],'MANUAL_REVIEW')
        self.assertTrue(self.s.safety.status('demo')['locked'])
        self.assertTrue(TrustaService(self.config).safety.status('demo')['locked'])
        self.assertEqual(self.s.db.rows('SELECT * FROM receipts'),[])
        self.assertTrue(self.s.finance.account('demo','acct-demo'))


if __name__=='__main__':
    unittest.main()

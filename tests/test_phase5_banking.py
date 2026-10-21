import ast
import copy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from backend.business import CHINA
from backend.config import Config
from backend.domain import Rejected, canonical, digest, uid
from backend.service import TrustaService
import tests.test_acceptance as original


class Phase5OriginalTransferAcceptance(original.Acceptance):
    def setUp(self):
        super().setUp()
        self.config=replace(self.config,communication_version=3,banking_version=5)
        self.service=TrustaService(self.config)


class BankingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config=Config(db_path=str(Path(self.temp.name)/'phase5.db'),signing_key=b'phase5-test-root',
                           communication_version=3,banking_version=5,strong_auth_secret='test-secret')
        self.s=TrustaService(self.config)

    def task(self,text):
        return self.s.create_task(text)

    def approve(self,task,index=0,approve=True):
        entry=task['action_records'][index]
        return self.s.banking.authorize(task['task_id'],'demo',digest(entry['action']),approve,'test-secret',entry['action']['action_id'])

    def ready_contract(self,text='冻结card-demo'):
        task=self.task(text)
        entry=task['action_records'][0]
        self.assertEqual(entry['status'],'WAITING_USER')
        auth=self.s.auth.decide(entry['authorization']['authorization_id'],'demo',entry['action'],True,'test-secret')
        contract=self.s.contracts.issue(entry['action'],entry['policy']['policy_decision_id'],auth['authorization_id'])
        return entry['action'],contract

    def card(self):
        return self.s.gateway.read('service','demo','card.get',resource_id='card-demo')

    def balance(self):
        return self.s.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo')['balance']

    def scheduled(self,seconds=5):
        stamp=datetime.fromtimestamp(time.time()+seconds,CHINA).strftime('%Y-%m-%d %H:%M:%S')
        task=self.task(f'在{stamp}给张三转100元')
        self.assertEqual(task['status'],'WAITING_USER')
        return task,self.s.banking.jobs(task['task_id'])[0]['execute_at']

    def test_six_registry_and_v3_service_identity(self):
        self.assertEqual(set(self.s.registry.agents.active()),{'supervisor','planner','transaction','finance','wealth','service'})
        self.assertEqual(len(self.s.crypto_registry.snapshot()['agents']),6)
        self.task('查询银行卡')
        self.assertTrue(self.s.db.one("SELECT * FROM replay_nonces WHERE sender='service'"))
        self.assertTrue(self.s.audit.verify_chain())

    def test_phase5_rejects_v2_and_downgrade(self):
        with self.assertRaises(Rejected):
            TrustaService(replace(self.config,communication_version=2))
        with self.assertRaises(Rejected):
            TrustaService(replace(self.config,banking_version=4))

    def test_phase4_migration_keeps_keys_receipt_proofs_and_suspended_status(self):
        other=replace(self.config,db_path=str(Path(self.temp.name)/'old.db'),banking_version=4)
        old=TrustaService(other)
        task=old.create_task('给张三转100元')
        done=old.authorize(task['task_id'],'demo',task['authorization']['action_hash'],True)
        old.anchors.run_once()
        fingerprint=old.crypto_registry.resolve('transaction')['public_key_fingerprint']
        old.crypto_registry.set_status('finance','SUSPENDED')
        new=TrustaService(replace(other,banking_version=5))
        self.assertEqual(new.crypto_registry.resolve('transaction')['public_key_fingerprint'],fingerprint)
        self.assertEqual(new.crypto_registry.resolve('finance')['status'],'SUSPENDED')
        self.assertEqual(new.get_task(task['task_id'])['receipt'],done['receipt'])
        self.assertTrue(new.records.query('demo',receipt_id=done['receipt']['receipt_id'])['record_verified'])

    def test_legacy_three_results_under_phase5(self):
        before=self.balance()
        bill=self.task('分析2026-09消费')
        wealth=self.task('推荐适合我的理财产品')
        self.assertEqual(bill['result']['summary']['net_spend'],542500)
        self.assertEqual([p['product_id'] for p in wealth['result']['recommendations']],['sandbox-r1','sandbox-r2'])
        self.assertEqual(self.balance(),before)
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])

    def test_card_freeze_unfreeze_limit_and_controls_real_state(self):
        self.assertEqual(self.approve(self.task('冻结card-demo'))['status'],'COMPLETED')
        self.assertEqual(self.card()['status'],'FROZEN')
        self.assertEqual(self.approve(self.task('解冻card-demo'))['status'],'COMPLETED')
        self.approve(self.task('card-demo限额设为1000元'))
        self.approve(self.task('设置card-demo交易控制线上关闭境外关闭非接触关闭'))
        card=self.card()
        self.assertEqual(card['spending_limit'],100000)
        self.assertEqual((card['online_enabled'],card['international_enabled'],card['contactless_enabled']),(0,0,0))
        self.assertEqual(self.balance(),1000000)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM bank_operations')),4)

    def test_lost_or_cancelled_card_never_unfreezes(self):
        for state in ('LOST','CANCELLED'):
            with self.s.db.transaction() as conn:
                conn.execute("UPDATE cards SET status=? WHERE id='card-demo'",(state,))
            task=self.task('解冻card-demo')
            self.assertEqual(task['status'],'BLOCKED')
            self.assertIsNone(task['action_records'][0]['authorization'])
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])

    def test_card_risk_increase_requires_strong_auth(self):
        task=self.task('card-demo限额设为8000元')
        entry=task['action_records'][0]
        self.assertEqual(entry['policy']['decision'],'STRONG_AUTH')
        with self.assertRaises(Rejected):
            self.s.authorize(task['task_id'],'demo',digest(entry['action']),True,'wrong')
        self.assertEqual(self.card()['spending_limit'],500000)

    def test_subscription_cancel_and_direct_debit_disable(self):
        for text,tool,resource,status in (('取消订阅sub-stream','subscription.get','sub-stream','CANCELLED'),
                                           ('关闭代扣dd-electric','direct_debit.get','dd-electric','DISABLED')):
            done=self.approve(self.task(text))
            self.assertEqual(done['action_records'][0]['verification']['status'],'VERIFIED')
            self.assertEqual(self.s.gateway.read('service','demo',tool,resource_id=resource)['status'],status)
            self.assertEqual(self.task(text)['status'],'BLOCKED')

    def test_non_cancelable_subscription_has_no_authorization(self):
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE subscriptions SET cancelable=0 WHERE id='sub-stream'")
        task=self.task('取消订阅sub-stream')
        self.assertEqual(task['status'],'BLOCKED')
        self.assertEqual(task['action_records'][0]['policy']['reason_codes'],['SUBSCRIPTION_NOT_CANCELABLE'])

    def test_detection_candidates_do_not_write_or_invent_authority(self):
        task=self.task('检测订阅')
        self.assertEqual(task['status'],'COMPLETED')
        self.assertEqual(len(task['result']['candidates']),3)
        self.assertEqual(sum(row['resolution']=='UNRESOLVED' for row in task['result']['resolutions']),1)
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])

    def test_cross_dag_dependencies_and_parallel_frontier(self):
        from backend.runtime.banking_plan import initial_plan
        t=self.s.supervisor.receive('demo','cross',{'task_type':'CROSS_SCENARIO'})
        self.s.blackboard.install_plan(t,{'task_id':t,'task_type':'CROSS_SCENARIO','steps':initial_plan('CROSS_SCENARIO')})
        self.s._step(t,'R01',lambda:None)
        self.assertEqual({step['step_id'] for step in self.s.scheduler.ready_steps(t)},{'R02','R03'})
        with self.assertRaises(Rejected):
            self.s.scheduler.start(t,'R05')

    def test_cross_independent_approvals_reject_one_branch(self):
        task=self.task('分析2026-09消费，找出可能的订阅，我逐项确认后取消')
        self.assertEqual(len(task['action_records']),2)
        self.assertTrue(all(task['agent_results'].get(key) for key in ('finance:READ_ONLY_REPORT','service:READ_ONLY_REPORT')))
        first,second=task['action_records']
        self.assertNotEqual(first['authorization']['authorization_id'],second['authorization']['authorization_id'])
        result=self.approve(task,0)
        self.assertEqual(result['status'],'WAITING_USER')
        result=self.approve(result,1,False)
        self.assertEqual(result['status'],'COMPLETED')
        statuses={row['id']:row['status'] for row in self.s.gateway.read('service','demo','subscription.list')}
        self.assertEqual(statuses[first['action']['target']['id']],'CANCELLED')
        self.assertEqual(statuses[second['action']['target']['id']],'ACTIVE')
        self.assertEqual(len(result['receipts']),1)

    def test_authorization_cannot_be_reused_for_another_action(self):
        action,contract=self.ready_contract('取消订阅sub-stream')
        other=self.task('取消订阅sub-cloud')['action_records'][0]['action']
        with self.assertRaises(Rejected):
            self.s.gateway.execute('executor',other,contract)
        with self.assertRaises(Rejected):
            self.s.auth.validate(contract['authorization_id'],other)
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])

    def test_all_business_agents_denied_every_write_capability(self):
        from backend.banking.actions import ACTION_SPECS
        action,contract=self.ready_contract()
        for actor in self.s.registry.agents.active():
            for capability in {spec['capability'] for spec in ACTION_SPECS.values()}|{'banking.transfer'}:
                with self.assertRaises(Rejected):
                    self.s.capabilities.check(actor,capability)
            with self.assertRaises(Rejected):
                self.s.gateway.execute(actor,action,contract)

    def test_suspended_service_cannot_read_or_propose(self):
        self.s.crypto_registry.set_status('service','SUSPENDED')
        task=self.task('冻结card-demo')
        self.assertEqual(task['status'],'BLOCKED')
        with self.assertRaises(Rejected):
            self.s.gateway.read('service','demo','card.list')

    def test_owner_isolation_all_resources_and_portfolio(self):
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO customers VALUES('other','other')")
            conn.execute("INSERT INTO accounts VALUES('acct-other','other','CNY',100000,0,0)")
            conn.execute("UPDATE cards SET customer_id='other',account_id='acct-other' WHERE id='card-demo'")
        self.assertEqual(self.task('冻结card-demo')['status'],'BLOCKED')
        with self.assertRaises(Rejected):
            self.s.gateway.read('wealth','demo','portfolio.get',account_id='acct-other',product_id='sandbox-r1')
        task=self.task('取消订阅sub-stream')
        with self.assertRaises(Rejected):
            self.s.banking.authorize(task['task_id'],'other',digest(task['proposed_actions'][0]),True)

    def test_weath_purchase_and_redeem_real_cash_portfolio_transaction_ledger(self):
        purchase=self.approve(self.task('购买sandbox-r1 1000元'))
        self.assertEqual(purchase['status'],'COMPLETED')
        self.assertEqual(self.balance(),900000)
        position=self.s.gateway.read('wealth','demo','portfolio.get',account_id='acct-demo',product_id='sandbox-r1')
        self.assertEqual(position['amount_cents'],100000)
        redeemed=self.approve(self.task('赎回sandbox-r1 400元'))
        self.assertEqual(redeemed['status'],'COMPLETED')
        self.assertEqual(self.balance(),940000)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM investment_transactions')),2)
        totals={row['side']:row['total'] for row in self.s.db.rows('SELECT side,SUM(amount) total FROM ledger_entries GROUP BY side')}
        self.assertEqual(totals,{'DEBIT':140000,'CREDIT':140000})
        self.assertEqual(self.s.db.one("SELECT balance FROM accounts WHERE id='investment-clearing'")['balance'],60000)

    def test_weath_deterministic_suitability_blocks_r2_r4(self):
        task=self.task('购买sandbox-r4 1000元')
        self.assertEqual(task['status'],'BLOCKED')
        self.assertIn('SUITABILITY_RISK_MISMATCH',task['action_records'][0]['policy']['rule_ids'])
        self.assertEqual(self.balance(),1000000)

    def test_weath_minimum_cash_inactive_currency_restrictions(self):
        for text,code in (('购买sandbox-r1 1元','BELOW_PRODUCT_MINIMUM'),('购买sandbox-r1 20000元','INSUFFICIENT_BALANCE'),
                           ('购买sandbox-inactive 100元','PRODUCT_INACTIVE')):
            self.assertIn(code,self.task(text)['action_records'][0]['policy']['rule_ids'])
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE product_restrictions SET purchase_enabled=0 WHERE product_id='sandbox-r1'")
        self.assertIn('PRODUCT_PURCHASE_RESTRICTED',self.task('购买sandbox-r1 100元')['action_records'][0]['policy']['rule_ids'])
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE wealth_products SET currency='USD' WHERE id='sandbox-r2'")
        self.assertIn('PRODUCT_CURRENCY_MISMATCH',self.task('购买sandbox-r2 1000元')['action_records'][0]['policy']['rule_ids'])

    def test_redemption_holding_period_and_excess(self):
        self.approve(self.task('购买sandbox-r2 1000元'))
        self.assertIn('HOLDING_PERIOD_NOT_MET',self.task('赎回sandbox-r2 100元')['action_records'][0]['policy']['rule_ids'])
        self.assertIn('INSUFFICIENT_HOLDING',self.task('赎回sandbox-r1 100元')['action_records'][0]['policy']['rule_ids'])

    def test_risk_change_after_approval_bank_atomic_recheck(self):
        action,contract=self.ready_contract('购买sandbox-r2 1000元')
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE risk_profiles SET risk_level=1 WHERE customer_id='demo'")
        with self.assertRaises(Rejected):
            self.s.gateway.execute('executor',action,contract)
        self.assertEqual(self.balance(),1000000)

    def test_strict_action_schema_and_target_tampering(self):
        action,contract=self.ready_contract('购买sandbox-r1 100元')
        for changed in (action|{'extra':'ignored'},action|{'parameters':{'amount':True,'currency':'CNY'}},
                        action|{'target':{'kind':'product','id':'sandbox-r4'}},action|{'requested_capability':'banking.card.freeze'}):
            with self.assertRaises(Rejected):
                self.s.gateway.execute('executor',changed,contract)
        self.assertEqual(self.balance(),1000000)

    def test_operation_idempotency_concurrent_and_hash_conflict(self):
        action,contract=self.ready_contract('购买sandbox-r1 100元')
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:self.s.gateway.execute('executor',action,contract),range(3)))
        self.assertEqual(len({row['id'] for row in results}),1)
        self.assertEqual(self.balance(),990000)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM investment_transactions')),1)

    def test_new_domain_bank_rechecks_deadline(self):
        action,contract=self.ready_contract()
        original_apply=self.s.gateway._adapter.apply_action
        def delayed(*args):
            with patch('time.time',return_value=contract['expires_at']+1):
                return original_apply(*args)
        with patch.object(self.s.gateway._adapter,'apply_action',side_effect=delayed),self.assertRaises(Rejected):
            self.s.gateway.execute('executor',action,contract)
        self.assertEqual(self.card()['status'],'ACTIVE')

    def test_domain_commit_lost_response_manual_review_no_retry(self):
        task=self.task('购买sandbox-r1 100元')
        original_apply=self.s.gateway._adapter.apply_action
        def lost(*args):
            original_apply(*args)
            raise TimeoutError('lost after commit')
        with patch.object(self.s.gateway._adapter,'apply_action',side_effect=lost):
            done=self.approve(task)
        self.assertEqual(done['status'],'MANUAL_REVIEW')
        self.assertEqual(self.balance(),990000)
        self.assertEqual(TrustaService(self.config).get_task(task['task_id'])['status'],'MANUAL_REVIEW')
        with self.assertRaises(Rejected):
            self.approve(done)
        with self.assertRaises(Rejected):
            self.s.banking.refresh(task['task_id'],'demo',task['proposed_actions'][0]['action_id'])

    def test_hard_verifier_detects_missing_investment_ledger(self):
        action,contract=self.ready_contract('购买sandbox-r1 100元')
        operation=self.s.gateway.execute('executor',action,contract)
        with self.s.db.transaction() as conn:
            conn.execute('DELETE FROM ledger_entries WHERE transaction_id=?',(operation['id'],))
        self.assertEqual(self.s.verifier.verify(action,operation['id'])['status'],'MANUAL_REVIEW')

    def test_scheduled_before_due_no_execution_due_valid_recheck_and_restart(self):
        task,due=self.scheduled()
        result=self.approve(task)
        self.assertEqual(result['status'],'WAITING_TIME')
        restarted=TrustaService(self.config)
        self.assertEqual(restarted.banking.tick()['processed'],0)
        with patch('time.time',return_value=due+1):
            self.assertEqual(restarted.banking.tick()['statuses'],['COMPLETED'])
            self.assertEqual(restarted.banking.tick()['processed'],0)
        self.assertEqual(self.balance(),990000)
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_schedule_expired_authorization_rebuilds_and_requires_reapproval(self):
        task,due=self.scheduled(400)
        self.approve(task)
        with patch('time.time',return_value=due+1):
            self.assertEqual(self.s.banking.tick()['statuses'],['WAITING_USER'])
            new=self.s.get_task(task['task_id'])
            self.assertEqual(self.balance(),1000000)
            self.assertNotEqual(digest(new['action_records'][-1]['action']),digest(task['action_records'][0]['action']))
            self.assertEqual(self.s.banking.jobs(task['task_id'])[0]['status'],'REAUTH_REQUIRED')
            done=self.approve(new,len(new['action_records'])-1)
            self.assertEqual(done['status'],'COMPLETED')
        self.assertEqual(self.balance(),990000)

    def test_schedule_account_balance_change_requires_new_exact_action(self):
        task,due=self.scheduled()
        self.approve(task)
        transfer=self.task('给张三转100元')
        self.s.authorize(transfer['task_id'],'demo',transfer['authorization']['action_hash'],True)
        with patch('time.time',return_value=due+1):
            self.s.banking.tick()
        self.assertEqual(self.balance(),990000)
        self.assertEqual(self.s.banking.jobs(task['task_id'])[0]['status'],'REAUTH_REQUIRED')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM transactions')),1)

    def test_schedule_policy_risk_upgrade_never_reuses_weak_auth(self):
        task,due=self.scheduled()
        self.approve(task)
        self.config.high_risk_threshold=1
        with patch('time.time',return_value=due+1):
            self.s.banking.tick()
            new=self.s.get_task(task['task_id'])
            self.assertEqual(new['action_records'][-1]['policy']['decision'],'STRONG_AUTH')
        self.assertEqual(self.balance(),1000000)

    def test_schedule_payee_disabled_no_write(self):
        task,due=self.scheduled()
        self.approve(task)
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE payee_contacts SET status='DISABLED' WHERE payee_id='payee-zhang'")
        with patch('time.time',return_value=due+1):
            self.s.banking.tick()
        self.assertEqual(self.balance(),1000000)

    def test_schedule_lost_commit_not_retried_on_restart(self):
        task,due=self.scheduled()
        self.approve(task)
        original_transfer=self.s.gateway._adapter.create_transfer
        def lost(*args):
            original_transfer(*args)
            raise TimeoutError('lost')
        with patch('time.time',return_value=due+1),patch.object(self.s.gateway._adapter,'create_transfer',side_effect=lost):
            self.s.banking.tick()
        restarted=TrustaService(self.config)
        self.assertEqual(restarted.banking.jobs(task['task_id'])[0]['status'],'MANUAL_REVIEW')
        self.assertEqual(restarted.banking.tick()['processed'],0)
        self.assertEqual(self.balance(),990000)

    def test_schedule_time_body_or_index_tamper_cannot_execute_early(self):
        for index_only in (False,True):
            task,due=self.scheduled(400)
            self.approve(task)
            job=self.s.banking.jobs(task['task_id'])[0]
            with self.s.db.transaction() as conn:
                row=conn.execute('SELECT body FROM scheduled_jobs WHERE schedule_id=?',(job['schedule_id'],)).fetchone()
                body=json.loads(row['body'])
                if not index_only:
                    body['execute_at']=time.time()-1
                conn.execute('UPDATE scheduled_jobs SET execute_at=?,body=? WHERE schedule_id=?',
                             (time.time()-1,canonical(body),job['schedule_id']))
            self.assertEqual(self.s.banking.tick()['statuses'],['MANUAL_REVIEW'])
            self.assertEqual(self.s.blackboard.get(task['task_id'])['status'],'MANUAL_REVIEW')
            with self.assertRaises(Rejected):
                self.s.banking.jobs(task['task_id'])
            self.assertEqual(self.balance(),1000000)
            self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_immediate_transfer_risk_upgrade_requires_new_strong_authorization(self):
        task=self.task('给张三转100元')
        self.config.high_risk_threshold=1
        result=self.s.authorize(task['task_id'],'demo',task['authorization']['action_hash'],True)
        self.assertEqual(result['status'],'WAITING_USER')
        self.assertEqual(result['authorization']['status'],'PENDING_STRONG_AUTH')
        self.assertEqual(self.balance(),1000000)
        done=self.s.authorize(task['task_id'],'demo',result['authorization']['action_hash'],True,'test-secret')
        self.assertEqual(done['status'],'COMPLETED')

    def test_boolean_snapshot_and_business_blackboard_authority_forgery_rejected(self):
        task=self.task('冻结card-demo')
        action=copy.deepcopy(task['action_records'][0]['action'])
        action['preconditions']['target']['online_enabled']=True
        action['expected_postconditions']['target']['online_enabled']=True
        with self.assertRaises(Rejected):
            self.s.schema.validate(action)
        for field in ('action_records','authorizations','verifications','receipts','scheduled_job_refs'):
            with self.assertRaises(Rejected):
                self.s.blackboard.update(task['task_id'],'service',**{field:[]})
        branch=self.s.planner.action_branch(task,'A99')
        with self.assertRaises(Rejected):
            self.s.blackboard.extend_action_plan(task['task_id'],'A99',branch[:2]+branch[3:])
        self.assertEqual(self.s.blackboard.get(task['task_id'])['steps'],task['steps'])

    def test_new_ir_schema_cannot_be_disabled_by_python_optimization(self):
        task=self.task('冻结card-demo')
        script="""
import sys
from backend.config import Config
from backend.service import TrustaService
from backend.domain import Rejected
s=TrustaService(Config(db_path=sys.argv[1],signing_key=b'phase5-test-root',communication_version=3,banking_version=5))
action=s.blackboard.get(sys.argv[2])['action_records'][0]['action']
action['schema_version']=99
try:
    s.schema.validate(action)
except Rejected as exc:
    if exc.code!='REJECTED_SCHEMA':
        raise SystemExit(2)
else:
    raise SystemExit(1)
"""
        result=subprocess.run([sys.executable,'-O','-c',script,self.config.db_path,task['task_id']],
                              cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_zero_card_limit_and_explicit_api_scheduled_account_missing_time(self):
        self.assertEqual(self.approve(self.task('card-demo限额设为0元'))['status'],'COMPLETED')
        self.assertEqual(self.card()['spending_limit'],0)
        stamp=datetime.fromtimestamp(time.time()+400,CHINA).strftime('%Y-%m-%d %H:%M:%S')
        task=self.s.create_task(f'在{stamp}给张三转100元',source_account_id='acct-savings')
        self.assertEqual(task['action_records'][0]['action']['source_account_id'],'acct-savings')
        incomplete=self.task('在2026-12-31给张三转100元')
        self.assertEqual(incomplete['task_type'],'SCHEDULED_TRANSFER')
        self.assertEqual(incomplete['status'],'WAITING_USER')
        self.assertEqual(incomplete['proposed_actions'],[])

    def test_non_disableable_direct_debit_rejected_by_policy_and_bank(self):
        action,contract=self.ready_contract('关闭代扣dd-electric')
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE direct_debits SET cancelable=0 WHERE id='dd-electric'")
        with self.assertRaises(Rejected):
            self.s.gateway.execute('executor',action,contract)
        task=self.task('关闭代扣dd-electric')
        self.assertEqual(task['status'],'BLOCKED')
        self.assertIn('DIRECT_DEBIT_NOT_DISABLEABLE',task['action_records'][0]['policy']['reason_codes'])
        self.assertEqual(self.s.gateway.read('service','demo','direct_debit.get',resource_id='dd-electric')['status'],'ACTIVE')
        self.assertEqual(self.s.db.rows('SELECT * FROM bank_operations'),[])

    def test_phone_alias_explicit_account_and_ambiguous_payee(self):
        for text in ('给13800000001转1元','给房东转1元','从acct-savings给张三转1元'):
            task=self.task(text)
            self.assertEqual(task['status'],'WAITING_USER')
            self.assertEqual(task['proposed_actions'][0]['recipient_id'],'payee-zhang')
        self.assertEqual(self.task('从acct-savings给张三转1元')['proposed_actions'][0]['source_account_id'],'acct-savings')
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE payee_contacts SET alias='房东' WHERE payee_id='payee-li'")
        self.assertEqual(self.task('给房东转1元')['status'],'WAITING_USER')
        self.assertIsNone(self.task('给房东转1元')['authorization'])

    def test_split_exact_minor_units_and_no_payouts(self):
        task=self.task('AA100.01元给张三、李四')
        self.assertEqual(task['status'],'COMPLETED')
        self.assertEqual([row['amount'] for row in task['result']['split_requests']],[5001,5000])
        self.assertEqual(self.balance(),1000000)
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_multi_turn_purchase_missing_amount_and_no_chat_approval(self):
        conv=self.s.conversations.create()
        first=self.s.conversations.send(conv['conversation_id'],'demo',uid(),0,'购买sandbox-r1')
        second=self.s.conversations.send(conv['conversation_id'],'demo',uid(),first['revision'],'100元')
        self.assertEqual(second['task']['status'],'WAITING_USER')
        third=self.s.conversations.send(conv['conversation_id'],'demo',uid(),second['revision'],'我确认')
        self.assertEqual(third['task']['status'],'WAITING_USER')
        self.assertEqual(self.balance(),1000000)

    def test_restart_domain_receipts_and_public_proof(self):
        task=self.approve(self.task('冻结card-demo'))
        receipt=task['action_records'][0]['receipt']
        self.s.anchors.run_once()
        rebuilt=TrustaService(self.config)
        self.assertEqual(rebuilt.get_task(task['task_id'])['receipts'],[receipt])
        self.assertTrue(rebuilt.records.query('demo',receipt_id=receipt['receipt_id'])['record_verified'])

    def test_agent_source_has_no_database_adapter_or_execute_calls(self):
        for path in (Path(__file__).parents[1]/'backend/agents').glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Attribute):
                    self.assertNotIn(node.attr,{'db','bank','_adapter','execute','apply_action','create_transfer'})


if __name__=='__main__':
    unittest.main()

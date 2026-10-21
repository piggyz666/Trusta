import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from backend.domain import Rejected,digest,canonical
from backend.llm.compact_graph import functions,normalize
from backend.runtime.handoff_evidence import verify_bundle
from backend.runtime.finance_transport import FinanceTransport
from tests.phase6_helpers import service
from tests.test_phase6_graph import read_graph


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=service(self.tmp.name)
        self.inputs={'input:account':'acct-demo','input:month':'2026-09'}

    def compact(self):
        options=functions(self.s.registry)
        def name(cap,owner):
            return next(k for k,v in options.items() if v['capability']==cap and v['owner']==owner and ('month' in v['parameters'] if cap=='transactions.read' else True))
        return {'schema_version':2,'clarification':None,'calls':[
            {'node_id':'Read','function':name('transactions.read','finance'),'parameters':{'account_id':'input:account','month':'input:month'},'dependencies':[]},
            {'node_id':'Sum','function':name('finance.summary','finance'),'parameters':{'records':'Read:result'},'dependencies':['Read']},
            {'node_id':'Report','function':name('graph.report','supervisor'),'parameters':{},'dependencies':['Sum']}]}

    def bundle(self):
        task=self.s.graphs.create('demo','analysis',read_graph(),self.inputs)
        bundle=self.s.handoffs.export(task['task_id'],'demo');snapshot=self.s.crypto_registry.snapshot()
        return bundle,{k['key_id']:k for k in snapshot['keys']},{v['agent_id']:v for v in snapshot['agents']}

    def test_compact_metadata_expansion_keeps_original_validator(self):
        status,graph=normalize(self.compact(),self.s.registry,self.inputs)
        self.assertEqual(status,'SUCCEEDED');self.assertEqual(graph,read_graph())

    def test_compact_does_not_extract_inline_amount_or_bank_records(self):
        for value in ([{'amount':999}],100,{'amount':100}):
            candidate=self.compact();candidate['calls'][1]['parameters']['records']=value
            with self.subTest(value=value),self.assertRaises(Rejected): normalize(candidate,self.s.registry,self.inputs)

    def test_compact_extra_authority_and_unknown_function_rejected(self):
        for change in ('unknown','extra','missing'):
            candidate=self.compact()
            if change=='unknown':candidate['calls'][0]['function']='executor/bank.write'
            if change=='extra':candidate['calls'][0]['authorization_id']='fake'
            if change=='missing':del candidate['calls'][0]['parameters']['month']
            with self.subTest(change=change),self.assertRaises(Rejected):normalize(candidate,self.s.registry,self.inputs)

    def test_compact_missing_ancestor_and_cycle_rejected(self):
        for dependencies in ([],['Sum']):
            candidate=self.compact();candidate['calls'][1]['dependencies']=dependencies
            with self.subTest(dependencies=dependencies),self.assertRaises(Rejected):normalize(candidate,self.s.registry,self.inputs)

    def test_compact_clarification_cannot_request_bank_facts(self):
        for name in ('records','authority','authorization_id'):
            with self.subTest(name=name),self.assertRaises(Rejected):normalize({'schema_version':2,'calls':[],
                'clarification':{'reason':'MISSING_INPUT','missing_parameters':[name]}},self.s.registry,self.inputs)

    def test_handoff_offline_verification_with_out_of_band_keys(self):
        bundle,keys,caps=self.bundle()
        self.assertTrue(verify_bundle(bundle,keys,caps))
        self.assertEqual(len(bundle['entries']),3)
        text=canonical(bundle)
        self.assertNotIn('张三',text);self.assertNotIn('merchant',text);self.assertNotIn('PRIVATE KEY',text)

    def test_handoff_tampered_parents_and_missing_causal_entry(self):
        bundle,keys,caps=self.bundle()
        broken=copy.deepcopy(bundle);broken['entries'][1]['evidence']['parents']=['0'*64]
        self.assertFalse(verify_bundle(broken,keys,caps))
        broken=copy.deepcopy(bundle);broken['entries'].pop(0)
        self.assertFalse(verify_bundle(broken,keys,caps))

    def test_handoff_wrong_key_owner_and_untrusted_key_rejected(self):
        bundle,keys,caps=self.bundle();wrong=copy.deepcopy(keys)
        key=bundle['entries'][0]['evidence']['message']['sender_key_id']
        wrong[key]['agent_id']='transaction'
        self.assertFalse(verify_bundle(bundle,wrong,caps));self.assertFalse(verify_bundle(bundle,{},caps))

    def test_handoff_capability_snapshot_or_plan_tamper_rejected(self):
        bundle,keys,caps=self.bundle();wrong=copy.deepcopy(caps);wrong['finance']['capability_hash']='0'*64
        self.assertFalse(verify_bundle(bundle,keys,wrong))
        broken=copy.deepcopy(bundle);broken['plan']['compiled']['steps'][0]['owner']='transaction'
        self.assertFalse(verify_bundle(broken,keys,caps))

    def test_handoff_duplicate_and_reordered_entries_rejected(self):
        bundle,keys,caps=self.bundle();broken=copy.deepcopy(bundle)
        broken['entries'].append(broken['entries'][0]);self.assertFalse(verify_bundle(broken,keys,caps))
        broken=copy.deepcopy(bundle);broken['entries'].reverse();self.assertFalse(verify_bundle(broken,keys,caps))

    def test_handoff_other_customer_export_denied(self):
        task=self.s.graphs.create('demo','analysis',read_graph(),self.inputs)
        with self.assertRaises(Rejected):self.s.handoffs.export(task['task_id'],'other')

    def test_nonce_audit_evidence_and_result_rollback_atomically(self):
        task=self.s.supervisor.receive('demo','transfer',{'task_type':'TRANSFER'})
        before=len(self.s.db.rows('SELECT * FROM replay_nonces'))
        with patch.object(self.s.handoffs,'record_in',side_effect=RuntimeError('injected crash')):
            with self.assertRaises(RuntimeError):self.s._agent_message('supervisor','planner',task,'T01','REQUEST',{})
        self.assertEqual(len(self.s.db.rows('SELECT * FROM replay_nonces')),before)
        self.assertEqual(self.s.blackboard.get(task)['agent_results'],{})
        self.assertEqual(self.s.db.rows('SELECT * FROM agent_handoff_evidence WHERE task_id=?',(task,)),[])
        self.s._agent_message('supervisor','planner',task,'T01','REQUEST',{})
        self.assertEqual(len(self.s.db.rows('SELECT * FROM replay_nonces')),before+1)

    def test_database_graph_claim_blocks_other_runtime_instance(self):
        task=self.s.supervisor.receive('demo','graph',{'task_type':'GENERAL_GRAPH'})
        second=service(self.tmp.name)
        with self.s.graphs.claims.hold(task):
            with self.assertRaises(Rejected) as exc:second.graphs.advance(task,'demo')
            self.assertEqual(exc.exception.code,'GRAPH_WORKER_BUSY')
        self.assertEqual(self.s.db.rows('SELECT * FROM graph_worker_claims'),[])

    def test_claim_cannot_be_taken_over_by_time_or_active_task_recovery(self):
        task=self.s.supervisor.receive('demo','graph',{'task_type':'GENERAL_GRAPH'})
        with self.s.graphs.claims.hold(task):
            with self.assertRaises(Rejected):self.s.graphs.claims.release_terminal(task,'demo')

    def test_terminal_claim_recovery_is_audited_and_owned(self):
        task=self.s.supervisor.receive('demo','graph',{'task_type':'GENERAL_GRAPH'})
        self.s.blackboard.update(task,'runtime',status='FAILED')
        with self.s.graphs.claims.hold(task):
            with self.assertRaises(Rejected):self.s.graphs.claims.release_terminal(task,'other')
            self.s.graphs.claims.release_terminal(task,'demo')


@unittest.skipUnless(os.name=='nt','AppContainer requires Windows; no isolation claim on unsupported hosts')
class ProcessTests(unittest.TestCase):
    def setUp(self):
        HardeningTests.setUp(self);self.s.finance_transport=FinanceTransport(self.s)
        self.addCleanup(self.s.finance_transport.close)

    def run_graph(self):
        task=self.s.graphs.create('demo','analysis',read_graph(),self.inputs)
        self.assertEqual(task['status'],'COMPLETED',task['errors'])
        self.assertEqual(task['result']['Sum:result']['net_spend'],542500)
        return task

    def test_real_process_roundtrip_signed_request_result_and_evidence(self):
        task=self.run_graph();transport=self.s.finance_transport
        self.assertNotEqual(transport.process.pid,os.getpid())
        self.assertTrue(transport.process.security['os_token_appcontainer'])
        jobs=[transport.decode(v) for v in self.s.db.rows('SELECT * FROM agent_delivery_jobs')]
        self.assertEqual([v['state'] for v in jobs],['APPLIED','APPLIED'])
        self.assertTrue(all(v['return_message']['signature'] for v in jobs))
        bundle=self.s.handoffs.export(task['task_id'],'demo');reg=self.s.crypto_registry.snapshot()
        self.assertTrue(verify_bundle(bundle,{v['key_id']:v for v in reg['keys']},{v['agent_id']:v for v in reg['agents']}))
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_compromised_child_cannot_read_key_database_or_adapter(self):
        self.run_graph();transport=self.s.finance_transport
        key=self.s.crypto_registry.store.path(self.s.crypto_registry.snapshot()['agents'][0]['key_id'])
        result=transport.process.exchange({'operation':'probe','paths':{'other_agent_key':str(key),
            'database':str(Path(self.s.db.path).resolve()),'source':str(Path('backend/banking/adapter.py').resolve())}})['probe']
        self.assertEqual(result,{'other_agent_key':'ACCESS_DENIED','database':'ACCESS_DENIED',
            'source':'ACCESS_DENIED','bank_adapter_import':'ACCESS_DENIED','secret_env_present':False})

    def packet(self):
        self.run_graph();transport=self.s.finance_transport
        job=transport.decode(self.s.db.rows('SELECT * FROM agent_delivery_jobs')[0])
        task=self.s.blackboard.get(job['task_id'])
        payload={'request_id':'test-read','task_id':task['task_id'],'step_id':'Read','revision':task['revision'],
            'capability':'transactions.read','values':{'account_id':'acct-demo','month':'2026-09'},'parent_hashes':[]}
        message=self.s.messages.sign('supervisor','finance',task['task_id'],'Read','REQUEST','handoff:test-read',payload)
        return transport,{'message':message,'payload':payload}

    def test_child_rejects_tampering_wrong_receiver_parent_refs_and_signature(self):
        transport,packet=self.packet()
        for mutation in ('signature','receiver','payload','parents','revision','owner'):
            candidate=copy.deepcopy(packet)
            if mutation=='signature':candidate['message']['signature']='bad'
            if mutation=='receiver':candidate['message']['receiver_agent']='transaction'
            if mutation=='payload':candidate['payload']['values']['account_id']='other'
            if mutation=='parents':candidate['payload']['parent_hashes']=['fake']
            if mutation=='revision':candidate['payload']['revision']+=1
            if mutation=='owner':candidate['message']['sender_agent']='transaction'
            with self.subTest(mutation=mutation):
                self.assertEqual(transport.process.exchange(candidate)['error'],'AGENT_PACKET_REJECTED')

    def test_child_replay_nonce_is_rejected(self):
        transport,packet=self.packet()
        self.assertIn('read_request',transport.process.exchange(packet))
        transport.process.exchange({'bank_read':[],'request_id':'test-read'})
        self.assertEqual(transport.process.exchange(packet)['error'],'AGENT_PACKET_REJECTED')

    def test_no_generic_signer_or_write_rpc_exists(self):
        self.run_graph();transport=self.s.finance_transport
        for packet in ({'sign_as':'transaction','body':{}},{'bank_write':'transfer.create'},
                       {'bootstrap_public_keys':{}},{'operation':'execute','action':{}}):
            with self.subTest(packet=packet):self.assertEqual(transport.process.exchange(packet)['error'],'AGENT_PACKET_REJECTED')

    def test_rotation_uses_new_public_snapshot_and_revocation_fails_closed(self):
        self.run_graph();self.s.crypto_registry.rotate('supervisor',0)
        self.s.crypto_registry.rotate('finance',0);self.run_graph()
        self.s.crypto_registry.set_status('finance','REVOKED')
        with self.assertRaises(Rejected) as exc:self.s.graphs.create('demo','analysis',read_graph(),self.inputs)
        self.assertEqual(exc.exception.code,'AGENT_REVOKED');self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_process_failure_never_falls_back_to_parent_agent(self):
        with patch('backend.runtime.finance_transport.ContainerProcess',side_effect=Rejected('AGENT_CONTAINER_LAUNCH_FAILED')):
            task=self.s.graphs.create('demo','analysis',read_graph(),self.inputs)
        self.assertEqual(task['status'],'BLOCKED');self.assertIn('AGENT_CONTAINER_LAUNCH_FAILED',task['errors'])
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])

    def test_outbox_storage_tamper_is_not_repaired(self):
        self.run_graph();row=self.s.db.rows('SELECT * FROM agent_delivery_jobs')[0]
        with self.s.db.transaction() as conn:conn.execute('UPDATE agent_delivery_jobs SET state=? WHERE id=?',('DISPATCHED',row['id']))
        with self.assertRaises(Rejected):self.s.finance_transport.decode(self.s.db.one('SELECT * FROM agent_delivery_jobs WHERE id=?',(row['id'],)))


if __name__=='__main__':unittest.main()

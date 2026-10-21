from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from backend.config import Config
from backend.service import TrustaService
from backend.audit_trust.anchor import configured_anchor
from scripts.scheduled_worker import run_once


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=Config(db_path=str(Path(self.tmp.name)/'bank.db'),signing_key=b'maintenance-tests',
                           communication_version=3,banking_version=5,safety_profile='competition')
        self.s=TrustaService(self.config)

    def test_shared_anchor_factory_no_silent_local_fallback(self):
        self.assertEqual(configured_anchor(self.s.db,self.config).name,'LOCAL_DEVELOPMENT')
        external=replace(self.config,blockchain_anchor_enabled=True)
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(configured_anchor(self.s.db,external).name,'BLOCKCHAIN_UNCONFIGURED')
        with patch.dict(os.environ,{'ANCHOR_RPC_URL':'https://example.org/rpc','ANCHOR_CONTRACT_ADDRESS':'0x'+'1'*40,'ANCHOR_CONFIRMATIONS':'4'},clear=True):
            # Construction performs no remote request and needs no wallet material.
            adapter=configured_anchor(self.s.db,external)
            self.assertEqual(adapter.name,'EXTERNAL_EVM_TESTNET')
            self.assertEqual(adapter.confirmations,4)

    def test_background_event_is_once_only_and_pending_authorization(self):
        due=time.time()+20
        event=self.s.events.create('demo','worker-event','acct-demo','BIRTHDAY_TRANSFER',due,{'recipient_name':'张三','amount':100})
        worker=TrustaService(self.config,recover_workflows=False)
        with patch('scripts.scheduled_worker.time.time',return_value=due+1):
            self.assertEqual(run_once(worker)['events_processed'],1)
            self.assertEqual(run_once(worker)['events_processed'],0)
        current=self.s.events.get(event['id'],'demo')
        self.assertEqual(current['status'],'PROPOSED')
        self.assertEqual(self.s.get_task(current['task_id'])['status'],'WAITING_USER')
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        self.assertEqual(self.s.db.rows('SELECT * FROM execution_contracts'),[])


if __name__=='__main__':unittest.main()

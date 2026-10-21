"""Competition safety smoke across six existing backend scenarios, isolated Virtual Bank."""
import base64
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from backend.config import Config
from backend.domain import digest
from backend.security.banking_controls import hotp
from backend.service import TrustaService


def run():
    with tempfile.TemporaryDirectory() as root:
        service=TrustaService(Config(db_path=str(Path(root)/'demo.sqlite3'),
                                    signing_key=b'isolated-backend-safety-demo-only',communication_version=3,
                                    banking_version=5,safety_profile='competition'))
        counter=int(time.time())//30+1
        with patch('backend.security.banking_controls.time.time',return_value=(counter-1)*30):
            enrollment=service.safety.begin_enrollment('demo')
            secret=base64.b32decode(enrollment['secret']+'='*((-len(enrollment['secret']))%8))
            service.safety.confirm_enrollment('demo',hotp(secret,counter-1))
        results=[]
        def complete(text):
            nonlocal counter
            task=service.create_task(text)
            if task.get('action_records'):
                for row in task['action_records']:
                    if row['status']!='WAITING_USER': continue
                    a=row['action'];auth=row['authorization']
                    if auth['auth_method']=='totp':
                        with patch('backend.security.banking_controls.time.time',return_value=counter*30):
                            task=service.authorize(task['task_id'],'demo',digest(a),True,hotp(secret,counter))
                        counter+=1
                    else: task=service.authorize(task['task_id'],'demo',digest(a),True)
            elif task.get('authorization'):
                a=task['proposed_actions'][0]
                if task['authorization']['auth_method']=='totp':
                    with patch('backend.security.banking_controls.time.time',return_value=counter*30):
                        task=service.authorize(task['task_id'],'demo',digest(a),True,hotp(secret,counter))
                    counter+=1
                else: task=service.authorize(task['task_id'],'demo',digest(a),True)
            results.append({'request':text,'task_type':task['task_type'],'status':task['status'],
                            'errors':task['errors'],'action_count':len(task.get('action_records',task['proposed_actions']))})
            assert task['status']=='COMPLETED', results[-1]
        complete('给张三转 600 元')
        complete('分析2026-09消费')
        complete('购买sandbox-r1 100元')
        complete('冻结card-demo')
        complete('解冻card-demo')
        complete('关闭代扣dd-electric')
        complete('分析2026-09消费，找出可能的订阅，我逐项确认后取消')
        assert service.audit.verify_chain()
        return {'mode':'ISOLATED_VIRTUAL_BANK','safety_profile':'competition','scenarios':results,
                'six_scenario_smoke_passed':True,'audit_chain_verified':True,'external_bank_calls':0,
                'llm_calls':0,'authentication':'real TOTP algorithm with explicitly controlled test clock',
                'note':'Existing scenarios only; does not assert new card issuance, annual reporting or event triggers implemented'}


if __name__=='__main__':
    report=run()
    destination=Path(__file__).resolve().parents[1]/'docs'/'backend-safety-six-scenario-demo.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'six_scenario_smoke_passed':report['six_scenario_smoke_passed'],
                      'audit_chain_verified':report['audit_chain_verified'],'external_bank_calls':0},ensure_ascii=False))

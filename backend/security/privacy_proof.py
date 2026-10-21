"""Real Groth16 privacy proofs, backed by a LOCAL sandbox issuer, never authorization.

Private balance, risk and blinding nonce exist only in memory. Public evidence proves
a task-bound predicate; all financial checks still run independently at execution.
"""
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from backend.domain import Rejected,canonical,digest,uid
from backend.security.crypto import LocalKeyStore,sign_object,verify_object,fingerprint

DOMAIN='trusta-local-eligibility-attestation-v1'
FILES={'banking_eligibility_js/banking_eligibility.wasm','banking_eligibility.zkey','verification_key.json'}


class PrivacyProofService:
    def __init__(self,service):
        self.s=service
        self.root=Path(service.config.zkp_artifact_dir).resolve()
        self.pin=service.config.zkp_manifest_sha256
        if service.config.banking_version!=5 or not service.crypto_registry:
            raise Rejected('ZKP_PROFILE_UNSUPPORTED')
        self.node=shutil.which('node')
        self.runner=Path(__file__).resolve().parents[2]/'crypto_trust'/'proof_runner.mjs'
        if not self.node or not (self.runner.parent/'node_modules'/'snarkjs').exists():
            raise Rejected('ZKP_RUNTIME_UNAVAILABLE')
        self.seal_key=hmac.new(service.config.signing_key,b'trusta-privacy-state-v1',hashlib.sha256).digest()
        self.compute=threading.BoundedSemaphore(1)
        self._assets()
        with service.lock,service.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS privacy_issuer(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS privacy_challenges(id TEXT PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL)')
            row=conn.execute('SELECT body FROM privacy_issuer WHERE id=1').fetchone()
            self.store=LocalKeyStore(Path(service.config.db_path).resolve().with_suffix('.proof-keys'),service.config.signing_key)
            if row:
                self.issuer=self._decode(row['body'])
            else:
                key_id='key-'+uid()
                self.issuer={'key_id':key_id,'public_key':self.store.generate(key_id),'authority':'TRUSTA_LOCAL_SANDBOX_ATTESTATION'}
                conn.execute('INSERT INTO privacy_issuer VALUES(1,?)',(self._encode(self.issuer),))
            self.store.load(self.issuer)

    @staticmethod
    def now(): return int(time.time()*1000)

    def _encode(self,body):
        return canonical({'body':body,'seal':hmac.new(self.seal_key,canonical(body).encode(),hashlib.sha256).hexdigest()})

    def _decode(self,raw):
        try:
            wrapped=json.loads(raw);body=wrapped['body']
            actual=hmac.new(self.seal_key,canonical(body).encode(),hashlib.sha256).hexdigest()
            if set(wrapped)!={'body','seal'} or not hmac.compare_digest(actual,wrapped['seal']): raise ValueError()
            return body
        except (ValueError,TypeError,KeyError): raise Rejected('ZKP_STATE_INVALID') from None

    def _assets(self):
        try:
            raw=(self.root/'manifest.json').read_bytes()
            if not re.fullmatch('[0-9a-f]{64}',self.pin) or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(),self.pin): raise ValueError()
            manifest=json.loads(raw)
            if (manifest['schema_version']!=1 or manifest['statement']!='BANKING_ELIGIBILITY_V1'
                or manifest['scheme']!='Groth16' or manifest['curve']!='bn128'
                or manifest['production_ready'] is not False or set(manifest['files'])!=FILES): raise ValueError()
            for name,expected in manifest['files'].items():
                path=self.root/name
                if path.resolve().is_relative_to(self.root) is False or hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise ValueError()
            self.manifest=manifest
        except (OSError,ValueError,TypeError,KeyError): raise Rejected('ZKP_ARTIFACT_TRUST_FAILED') from None

    def status(self):
        return {'enabled':True,'implemented':True,'scheme':'Groth16','statement':'BANKING_ELIGIBILITY_V1',
                'setup':self.manifest['setup'],'phase1':self.manifest.get('phase1'),
                'production_ready':False,'issuer':'LOCAL_SANDBOX','financial_authority':False}

    def _run(self,operation,**parameters):
        self._assets()
        if not self.compute.acquire(blocking=False): raise Rejected('ZKP_BUSY')
        try:
            result=subprocess.run([self.node,str(self.runner)],input=canonical({'operation':operation,'root':str(self.root),**parameters}),
                                  capture_output=True,text=True,encoding='utf-8',timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or len(result.stdout)>16384: raise Rejected('PRIVACY_PROOF_REJECTED')
            return json.loads(result.stdout)
        except (OSError,ValueError,subprocess.TimeoutExpired): raise Rejected('PRIVACY_PROOF_REJECTED') from None
        finally: self.compute.release()

    def _facts(self,task_id,owner,action_hash):
        provider=self.s.gateway._adapter.provider.manifest
        if provider.provider!='virtual' or provider.environment!='local': raise Rejected('ZKP_ISSUER_UNSUPPORTED')
        task=self.s._owned_task(task_id,owner)
        if task['status']!='WAITING_USER': raise Rejected('ZKP_TASK_NOT_PENDING')
        actions=[record['action'] for record in task.get('action_records',[])]+task.get('proposed_actions',[])
        matches=[a for a in actions if digest(a)==action_hash]
        if not matches: raise Rejected('ZKP_ACTION_MISMATCH')
        action=matches[0]
        if action['action_type'] not in {'TRANSFER','INVESTMENT_PURCHASE'} or action['expires_at']*1000<=self.now():
            raise Rejected('ZKP_ACTION_UNSUPPORTED')
        self.s.safety.assert_unlocked(owner)
        account=self.s.gateway.read('wealth',owner,'account.get',account_id=action['source_account_id'])
        profile=self.s.wealth.risk_profile(owner)
        if account['frozen'] or account['currency']!='CNY': raise Rejected('ZKP_ACCOUNT_UNSUPPORTED')
        amount=action['amount'] if action['action_type']=='TRANSFER' else action['parameters']['amount']
        required=1
        if action['action_type']=='INVESTMENT_PURCHASE':
            product=next((p for p in self.s.wealth.products(owner) if p['id']==action['target']['id']),None)
            if not product: raise Rejected('PRODUCT_NOT_FOUND')
            required=product['risk_level']
        if any(type(v) is not int for v in (account['balance'],profile['risk_level'],amount,required)) or not 0<amount<2**64:
            raise Rejected('ZKP_INVALID_FACTS')
        facts={'account':account,'risk':profile,'amount':amount,'required':required}
        # Secret HMAC prevents dictionary recovery of small-domain private facts.
        stamp=hmac.new(self.seal_key,canonical(facts).encode(),hashlib.sha256).hexdigest()
        return task,action,facts,stamp

    def prove(self,task_id,owner,action_hash):
        with self.s.lock:
            task,action,facts,stamp=self._facts(task_id,owner,action_hash)
            challenge_id=uid();expires=min(self.now()+60000,int(action['expires_at']*1000))
            context=int(digest({'challenge_id':challenge_id,'owner':owner,'task':task_id,'action':action_hash,'expires':expires})[:62],16)
            witness={'balance':str(facts['account']['balance']),'risk':str(facts['risk']['risk_level']),
                     'nonce':str(secrets.randbits(248)),'amount':str(facts['amount']),
                     'requiredRisk':str(facts['required']),'context':str(context)}
        commitment=self._run('commit',witness=witness)['commitment']
        output=self._run('prove',witness=witness)
        expected=[commitment,witness['amount'],witness['requiredRisk'],witness['context']]
        if output['publicSignals']!=expected: raise Rejected('ZKP_STATEMENT_MISMATCH')
        attestation={'schema_version':1,'challenge_id':challenge_id,'task_id':task_id,'action_hash':action_hash,
                     'public_signals':expected,'expires_at':expires,'manifest_sha256':self.pin,
                     'issuer_fingerprint':fingerprint(self.issuer['public_key']),
                     'authority':self.issuer['authority'],'financial_authority':False}
        signature=sign_object(self.store.load(self.issuer),DOMAIN,attestation)
        with self.s.lock:
            if self._facts(task_id,owner,action_hash)[3]!=stamp or self.now()>=expires: raise Rejected('ZKP_FACTS_STALE')
            state={'id':challenge_id,'owner':owner,'task':task_id,'action_hash':action_hash,'attestation':attestation,
                   'signature':signature,'source_stamp':stamp,'consumed':False}
            with self.s.db.transaction() as conn:
                conn.execute('INSERT INTO privacy_challenges VALUES(?,?,?)',(challenge_id,owner,self._encode(state)))
                self.s.audit.append_in(conn,task_id,None,'PRIVACY_PROOF_CREATED','privacy_proof',
                                       {'challenge_id':challenge_id,'statement':'BANKING_ELIGIBILITY_V1','financial_authority':False})
        return {'attestation':attestation,'signature':signature,'proof':output['proof'],'public_signals':expected,
                'issuer_public_key':self.issuer['public_key'],'production_ready':False}

    def _challenge(self,owner,bundle,conn):
        if not isinstance(bundle,dict) or set(bundle)!={'attestation','signature','proof','public_signals','issuer_public_key','production_ready'}:
            raise Rejected('INVALID_PRIVACY_PROOF')
        att=bundle['attestation']
        if not isinstance(att,dict) or not isinstance(att.get('challenge_id'),str): raise Rejected('INVALID_PRIVACY_PROOF')
        row=conn.execute('SELECT * FROM privacy_challenges WHERE id=? AND owner=?',(att['challenge_id'],owner)).fetchone()
        if not row: raise Rejected('ZKP_ACCESS_DENIED')
        state=self._decode(row['body'])
        if state['owner']!=owner or state['id']!=row['id']: raise Rejected('ZKP_STATE_INVALID')
        if state['consumed']: raise Rejected('ZKP_REPLAY_DENIED')
        if self.now()>=state['attestation']['expires_at']: raise Rejected('ZKP_EXPIRED')
        if (att!=state['attestation'] or bundle['signature']!=state['signature']
            or bundle['public_signals']!=att['public_signals'] or bundle['production_ready'] is not False
            or bundle['issuer_public_key']!=self.issuer['public_key']): raise Rejected('ZKP_STATEMENT_MISMATCH')
        verify_object(self.issuer['public_key'],DOMAIN,att,bundle['signature'])
        return state

    def verify(self,owner,bundle):
        with self.s.lock:
            with self.s.db.transaction() as conn: state=self._challenge(owner,bundle,conn)
            if self._facts(state['task'],owner,state['action_hash'])[3]!=state['source_stamp']: raise Rejected('ZKP_FACTS_STALE')
        if not self._run('verify',proof=bundle['proof'],publicSignals=bundle['public_signals']).get('verified'):
            raise Rejected('PRIVACY_PROOF_REJECTED')
        with self.s.lock:
            if self._facts(state['task'],owner,state['action_hash'])[3]!=state['source_stamp']: raise Rejected('ZKP_FACTS_STALE')
            with self.s.db.transaction() as conn:
                state=self._challenge(owner,bundle,conn) # Atomic once-only consumption after fresh reads.
                state['consumed']=True
                conn.execute('UPDATE privacy_challenges SET body=? WHERE id=?',(self._encode(state),state['id']))
                self.s.audit.append_in(conn,state['task'],None,'PRIVACY_PROOF_VERIFIED','privacy_proof',
                                       {'challenge_id':state['id'],'financial_authority':False})
        return {'verified':True,'challenge_id':state['id'],'statement':'BANKING_ELIGIBILITY_V1',
                'financial_authority':False,'bank_write_performed':False,'production_ready':False}

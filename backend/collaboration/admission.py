"""M1 admission is distinct from capabilities, policy and financial authority.

Only a separately pinned evaluator may attest public inputs. An Agent's own
signature, self-reported score or a mathematically valid self-issued proof is
insufficient. Proof setup remains explicitly developmental.
"""
import hashlib
import hmac
import json
import time
from backend.domain import Rejected, canonical, digest
from backend.security.crypto import fingerprint, verify_object
from backend.collaboration.capability_proof import STATEMENT, statement_hash

EVALUATOR_DOMAIN = 'TRUSTA-ANCHOR-M1-EVALUATOR-V1'


class CapabilityAdmission:
    def __init__(self, service, identity, engine, *, policy, evaluator_public_key,
                 clock=time.time):
        self.s, self.identity, self.engine = service, identity, engine
        self.policy, self.evaluator_key, self.clock = dict(policy), evaluator_public_key, clock
        if (set(policy) != {'version','epoch','threshold','benchmark_root','evaluator_version','max_age_seconds'}
                or policy['version'] != 'TRUSTA-M1-BANK-READONLY-V1'
                or type(policy['epoch']) is not int or policy['epoch'] < 0
                or type(policy['threshold']) is not int or not 0 <= policy['threshold'] <= 1000000
                or type(policy['max_age_seconds']) is not int or not 1 <= policy['max_age_seconds'] <= 86400):
            raise Rejected('ANCHOR_ADMISSION_POLICY_INVALID')
        self.policy_id = digest(policy)
        self.seal_key = hmac.new(service.config.signing_key,b'TRUSTA-ANCHOR-M1-STATE-V1',hashlib.sha256).digest()
        with self.s.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS collaboration_admission(agent TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _encode(self, record):
        return canonical({'record':record,'seal':hmac.new(self.seal_key,canonical(record).encode(),hashlib.sha256).hexdigest()})

    def _decode(self, value):
        try:
            wrapper = json.loads(value)
            record = wrapper['record']
            if (set(wrapper) != {'record','seal'} or not hmac.compare_digest(wrapper['seal'],
                    hmac.new(self.seal_key,canonical(record).encode(),hashlib.sha256).hexdigest())):
                raise ValueError()
            return record
        except (KeyError,ValueError,TypeError):
            raise Rejected('ANCHOR_ADMISSION_STATE_TAMPERED') from None

    def expected_statement(self, binding):
        b = binding['binding']
        return {'version':STATEMENT,'protocol_domain':'TRUSTA-ANCHOR-P256-V1','chain_id':101,
                'did':b['did'],'message_key_hash':fingerprint(b['public_key']),
                'model_commitment':b['model_commitment'],'benchmark_root':self.policy['benchmark_root'],
                'evaluator_version':self.policy['evaluator_version'],'threshold':self.policy['threshold'],
                'policy_id':self.policy_id,'epoch':self.policy['epoch'],'score_encoding':'anchor-f1-micro-v1'}

    def _attestation(self, attestation, proof, statement):
        try:
            body = attestation['body']
            verify_object(self.evaluator_key,EVALUATOR_DOMAIN,body,attestation['signature'])
            now = int(self.clock())
            if (set(body) != {'version','statement_hash','public_signals_hash','manifest_sha256',
                             'evidence_hash','issued_at','expires_at','scope'}
                    or body['version'] != 1 or body['statement_hash'] != statement_hash(statement)
                    or body['public_signals_hash'] != digest(proof['public_signals'])
                    or body['manifest_sha256'] != self.engine.pin
                    or body['scope'] != 'RESTRICTED_CAPABILITY_EVALUATION_NO_FINANCIAL_AUTHORITY'
                    or type(body['issued_at']) is not int or type(body['expires_at']) is not int
                    or not body['issued_at'] <= now < body['expires_at']
                    or not 0 < body['expires_at']-body['issued_at'] <= self.policy['max_age_seconds']
                    or len(body['evidence_hash']) != 64):
                raise ValueError()
            int(body['evidence_hash'],16)
        except (KeyError,ValueError,TypeError):
            raise Rejected('ANCHOR_EVALUATOR_ATTESTATION_INVALID') from None

    def admit(self, agent, *, binding, proof, attestation):
        if binding['binding']['epoch'] != self.policy['epoch'] or binding['binding']['policy_id'] != self.policy_id:
            raise Rejected('ANCHOR_ADMISSION_EPOCH_POLICY_MISMATCH')
        statement = self.expected_statement(binding)
        self._attestation(attestation,proof,statement)
        self.identity.verify_binding(agent,binding)
        verification = self.engine.verify(proof,statement,proof['public_signals'])
        record = {'agent':agent,'binding':binding,'proof':proof,'attestation':attestation,
                  'verification':verification,'policy_id':self.policy_id,'revoked':False}
        with self.s.lock, self.s.db.transaction() as conn:
            previous=conn.execute('SELECT body FROM collaboration_admission WHERE agent=?',(agent,)).fetchone()
            if previous:
                old=self._decode(previous['body']);old_binding=old['binding']['binding']
                old_epoch=old_binding['epoch'];new_epoch=binding['binding']['epoch']
                if (new_epoch<old_epoch or new_epoch==old_epoch and
                        (old['revoked'] or digest(old_binding)!=digest(binding['binding']))):
                    raise Rejected('ANCHOR_ADMISSION_REVOKED_NEW_EPOCH_REQUIRED')
            conn.execute('INSERT OR REPLACE INTO collaboration_admission VALUES(?,?)',(agent,self._encode(record)))
            self.s.audit.append_in(conn,None,None,'ANCHOR_M1_ADMITTED','identity_registry',
                                   {'agent':agent,'policy_id':self.policy_id,'proof_hash':digest(proof)})
        return verification

    def _current(self, agent, policy_id, model):
        row=self.s.db.one('SELECT body FROM collaboration_admission WHERE agent=?',(agent,))
        if row is None:
            raise Rejected('ANCHOR_NOT_ADMITTED')
        record=self._decode(row['body'])
        b=record['binding']['binding']
        if (record['revoked'] or record['policy_id'] != self.policy_id or policy_id != self.policy_id
                or b['model_commitment'] != model or b['epoch'] != self.policy['epoch']):
            raise Rejected('ANCHOR_ADMISSION_NOT_CURRENT')
        statement=self.expected_statement(record['binding'])
        self._attestation(record['attestation'],record['proof'],statement)
        if record['verification']['proof_hash'] != digest(record['proof']):
            raise Rejected('ANCHOR_ADMISSION_PROOF_CHANGED')
        self.identity.verify_binding(agent,record['binding'])
        return record

    def snapshot(self, agents, policy_id, models):
        members={}
        for agent in agents:
            record=self._current(agent,policy_id,models[agent]); b=record['binding']['binding']
            members[agent]={k:b[k] for k in ('did','key_id','public_key','capability_hash','epoch',
                                            'model_commitment','identity_uri')}
            members[agent]['admission_hash']=digest(record)
            members[agent]['admission_evidence']=record
        return {'version':1,'scope':'OFFICIAL_WEID_REAL_GROTH16_DEVELOPMENT_SETUP',
                'policy_id':policy_id,'policy':self.policy,'members':members,
                'evaluator_key_hash':fingerprint(self.evaluator_key),'manifest_sha256':self.engine.pin}

    def check_current(self, agent, member, policy_id):
        record=self._current(agent,policy_id,member['model_commitment'])
        if digest(record) != member['admission_hash']:
            raise Rejected('ANCHOR_ADMISSION_SNAPSHOT_CHANGED')

    def check_current_in(self, conn, agent, member, policy_id):
        """Recheck local revocation atomically with V3 nonce consumption."""
        row=conn.execute('SELECT body FROM collaboration_admission WHERE agent=?',(agent,)).fetchone()
        if not row: raise Rejected('ANCHOR_NOT_ADMITTED')
        record=self._decode(row['body'])
        if record['revoked'] or record['policy_id'] != policy_id or digest(record) != member['admission_hash']:
            raise Rejected('ANCHOR_ADMISSION_SNAPSHOT_CHANGED')
        self._attestation(record['attestation'],record['proof'],self.expected_statement(record['binding']))

    def revoke(self, agent):
        # Operator-only object API: never exposed as an Agent tool.
        with self.s.lock,self.s.db.transaction() as conn:
            row=conn.execute('SELECT body FROM collaboration_admission WHERE agent=?',(agent,)).fetchone()
            if not row: raise Rejected('ANCHOR_NOT_ADMITTED')
            record=self._decode(row['body']);record['revoked']=True
            conn.execute('UPDATE collaboration_admission SET body=? WHERE agent=?',(self._encode(record),agent))
            self.s.audit.append_in(conn,None,None,'ANCHOR_M1_REVOKED','identity_registry',{'agent':agent,'policy_id':self.policy_id})

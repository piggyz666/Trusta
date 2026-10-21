"""Admission authorization negatives. Crypto itself uses separate real-proof tests."""
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric import ec
from backend.domain import Rejected,digest
from backend.security.crypto import public_pem,sign_object
from backend.collaboration.admission import CapabilityAdmission,EVALUATOR_DOMAIN
from backend.collaboration.capability_proof import build_witness
from tests.phase6_helpers import service


class IdentityUnitFixture:
    def __init__(self,s):self.s=s;self.available=True
    def verify_binding(self,agent,bundle):
        if not self.available:raise Rejected('UNIT_NETWORK_UNAVAILABLE')
        self.s.crypto_registry.require_active(agent)
        if self.s.crypto_registry.resolve(agent)['key_id']!=bundle['binding']['key_id']:
            raise Rejected('UNIT_BINDING_STALE')


class ProofUnitFixture:
    """Unit-only crypto seam, never imported by an integration runner."""
    pin='a'*64
    def verify(self,bundle,statement,signals):
        if bundle['statement']!=statement:raise Rejected('UNIT_WRONG_STATEMENT')
        return {'verified':True,'proof_hash':digest(bundle),'financial_authority':False}


class AdmissionAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=service(self.temp.name);self.now=10000
        self.key=ec.generate_private_key(ec.SECP256R1());self.identity=IdentityUnitFixture(self.s)
        self.policy={'version':'TRUSTA-M1-BANK-READONLY-V1','epoch':1,'threshold':800000,
                     'benchmark_root':'b'*64,'evaluator_version':'c'*64,'max_age_seconds':100}
        self.a=CapabilityAdmission(self.s,self.identity,ProofUnitFixture(),policy=self.policy,
                                   evaluator_public_key=public_pem(self.key.public_key()),clock=lambda:self.now)
        ident=self.s.crypto_registry.resolve('finance')
        self.binding={'binding':{'did':'did:weid:101:0x'+'1'*40,'key_id':ident['key_id'],
            'public_key':ident['public_key'],'capability_hash':ident['capability_hash'],
            'identity_uri':ident['identity_uri'],'epoch':1,'policy_id':self.a.policy_id,'model_commitment':'d'*64}}
        self.statement=self.a.expected_statement(self.binding)
        _,signals=build_witness(self.statement,tp=20,fp=0,fn=0,prediction_hash='e'*64)
        self.proof={'statement':self.statement,'public_signals':signals,'test_scope':'UNIT_NO_PROOF_CLAIM'}
        from backend.collaboration.capability_proof import statement_hash
        self.body={'version':1,'statement_hash':statement_hash(self.statement),'public_signals_hash':digest(signals),
                   'manifest_sha256':self.a.engine.pin,'evidence_hash':'f'*64,'issued_at':self.now,
                   'expires_at':self.now+100,'scope':'RESTRICTED_CAPABILITY_EVALUATION_NO_FINANCIAL_AUTHORITY'}

    def attestation(self,key=None):
        return {'body':self.body,'signature':sign_object(key or self.key,EVALUATOR_DOMAIN,self.body)}

    def admit(self,attestation=None):
        return self.a.admit('finance',binding=self.binding,proof=self.proof,attestation=attestation or self.attestation())

    def snapshot(self):return self.a.snapshot(['finance'],self.a.policy_id,{'finance':'d'*64})

    def test_trusted_evaluator_required(self):
        attacker=ec.generate_private_key(ec.SECP256R1())
        with self.assertRaises(Rejected):self.admit(self.attestation(attacker))

    def test_agent_self_signature_not_evaluator(self):
        body,sig=self.s.crypto_registry.sign('finance',EVALUATOR_DOMAIN,lambda _:self.body)
        with self.assertRaises(Rejected):self.admit({'body':body,'signature':sig})

    def test_valid_authorization_install_and_snapshot(self):
        self.admit();self.assertIn('finance',self.snapshot()['members'])

    def test_expired_attestation_rejected(self):
        self.now+=100
        with self.assertRaises(Rejected):self.admit()

    def test_future_attestation_rejected(self):
        self.body['issued_at']+=1
        with self.assertRaises(Rejected):self.admit()

    def test_overlong_lifetime_rejected(self):
        self.body['expires_at']+=1
        with self.assertRaises(Rejected):self.admit()

    def test_boolean_time_rejected(self):
        self.body['issued_at']=True
        with self.assertRaises(Rejected):self.admit()

    def test_wrong_verification_key_attestation_rejected(self):
        self.body['manifest_sha256']='0'*64
        with self.assertRaises(Rejected):self.admit()

    def test_attested_public_inputs_tamper_rejected(self):
        att=self.attestation();self.proof['public_signals'][24]='1'
        with self.assertRaises(Rejected):self.admit(att)

    def test_wrong_epoch_rejected(self):
        self.binding['binding']['epoch']=2
        with self.assertRaises(Rejected):self.admit()

    def test_changed_model_rejected(self):
        self.admit()
        with self.assertRaises(Rejected):self.a.snapshot(['finance'],self.a.policy_id,{'finance':'e'*64})

    def test_weid_failure_never_downgrades(self):
        self.admit();self.identity.available=False
        with self.assertRaises(Rejected):self.snapshot()

    def test_revoked_admission_excluded(self):
        self.admit();self.a.revoke('finance')
        with self.assertRaises(Rejected):self.snapshot()

    def test_revocation_cannot_be_undone_by_reinstalling_proof(self):
        self.admit();self.a.revoke('finance')
        with self.assertRaises(Rejected):self.admit()

    def test_database_state_tamper_rejected(self):
        self.admit()
        with self.s.db.transaction() as conn:conn.execute("UPDATE collaboration_admission SET body='{}'")
        with self.assertRaises(Rejected):self.snapshot()

    def test_key_rotation_requires_new_bound_proof(self):
        self.admit();self.s.crypto_registry.rotate('finance',0)
        with self.assertRaises(Rejected):self.snapshot()

    def test_restart_preserves_revocation(self):
        self.admit();self.a.revoke('finance')
        restored=CapabilityAdmission(self.s,self.identity,ProofUnitFixture(),policy=self.policy,
                                      evaluator_public_key=public_pem(self.key.public_key()),clock=lambda:self.now)
        with self.assertRaises(Rejected):restored.snapshot(['finance'],restored.policy_id,{'finance':'d'*64})

    def advance_epoch(self):
        self.policy=self.policy|{'epoch':2}
        self.a=CapabilityAdmission(self.s,self.identity,ProofUnitFixture(),policy=self.policy,
            evaluator_public_key=public_pem(self.key.public_key()),clock=lambda:self.now)
        self.binding['binding']|={'epoch':2,'policy_id':self.a.policy_id}
        self.statement=self.a.expected_statement(self.binding)
        _,signals=build_witness(self.statement,tp=20,fp=0,fn=0,prediction_hash='e'*64)
        self.proof={'statement':self.statement,'public_signals':signals,'test_scope':'UNIT_NO_PROOF_CLAIM'}
        from backend.collaboration.capability_proof import statement_hash
        self.body|={'statement_hash':statement_hash(self.statement),'public_signals_hash':digest(signals)}

    def test_revoked_admission_can_recover_only_with_new_epoch_and_attested_proof(self):
        self.admit();self.a.revoke('finance');self.advance_epoch();self.admit()
        self.assertEqual(self.snapshot()['members']['finance']['epoch'],2)

    def test_lower_epoch_cannot_replace_newer_admission(self):
        self.admit();old_binding=__import__('copy').deepcopy(self.binding);old_proof=self.proof
        old_statement=self.statement;old_body=dict(self.body);old_policy=self.policy
        self.advance_epoch();self.admit()
        self.binding,self.proof,self.statement,self.body=old_binding,old_proof,old_statement,old_body
        self.a=CapabilityAdmission(self.s,self.identity,ProofUnitFixture(),policy=old_policy,
            evaluator_public_key=public_pem(self.key.public_key()),clock=lambda:self.now)
        with self.assertRaises(Rejected):self.admit()

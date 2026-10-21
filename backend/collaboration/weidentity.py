"""Explicit official-SDK identity adapter for the isolated FISCO test network.

No existing operator key is read. P-256 message keys remain separate from WeID
control keys. A failed SDK operation never falls back to did:key or JSON DIDs.
"""
import json
from pathlib import Path
import subprocess
import threading
from backend.domain import Rejected, digest
from backend.security.crypto import fingerprint, verify_object
from backend.security.crypto import HEX
from backend.collaboration.process import wsl_executable

DOMAIN = 'TRUSTA-ANCHOR-WEID-BINDING-V1'
SDK_SLOTS=threading.BoundedSemaphore(2)


class WeIdentityLab:
    def __init__(self, service):
        self.s = service
        self.workspace = Path(__file__).resolve().parents[2]
        self.root = self.workspace / 'data/anchor-integration/weidentity-lab'
        self.script = 'scripts/anchor_weidentity_bridge.sh'
        self.health = self.call('health')
        if (self.health.get('chain_id') != 101 or self.health.get('group_id') != 1
                or self.health.get('authority') != 'OFFICIAL_SDK_REAL_FISCO_TESTNET'):
            raise Rejected('ANCHOR_WEID_NETWORK_MISMATCH')

    def call(self, operation, **values):
        if operation not in {'health', 'register', 'query', 'bind', 'deactivate', 'reactivate'}:
            raise Rejected('ANCHOR_WEID_OPERATION_FORBIDDEN')
        if not SDK_SLOTS.acquire(blocking=False):
            raise Rejected('ANCHOR_WEID_BUSY_NO_FALLBACK')
        try:
            result = subprocess.run([wsl_executable(), '-d', 'Ubuntu-22.04', '--', 'bash', self.script],
                                    cwd=self.workspace, input=json.dumps({'operation':operation,**values}).encode(),
                                    capture_output=True, timeout=90,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            lines = [line[12:] for line in result.stdout.decode('utf-8', errors='strict').splitlines()
                     if line.startswith('TRUSTA_JSON=')]
            if result.returncode or len(lines) != 1 or len(lines[0]) > 65536:
                raise ValueError()
            value = json.loads(lines[0])
            if value.get('chain_id') != 101 or 'error' in value:
                raise ValueError()
            return value
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            raise Rejected('ANCHOR_WEID_UNAVAILABLE_NO_FALLBACK') from None
        finally:
            SDK_SLOTS.release()

    def register_binding(self, agent, *, epoch, model_commitment, policy_id):
        if (agent not in self.s.registry.agents.active() or type(epoch) is not int or not 0<=epoch<2**31
                or not isinstance(model_commitment,str) or not HEX.fullmatch(model_commitment)
                or not isinstance(policy_id,str) or not HEX.fullmatch(policy_id)):
            raise Rejected('ANCHOR_WEID_BINDING_INVALID')
        self.s.crypto_registry.require_active(agent)
        created = self.call('register', agent=agent)
        did = created['did']
        if not did.startswith('did:weid:101:0x'):
            raise Rejected('ANCHOR_WEID_DOCUMENT_MISMATCH')
        def body(identity):
            return {'version':1, 'agent':agent, 'did':did, 'chain_id':101,
                    'identity_uri':identity['identity_uri'], 'key_id':identity['key_id'],
                    'public_key':identity['public_key'], 'public_key_fingerprint':fingerprint(identity['public_key']),
                    'capability_hash':identity['capability_hash'], 'epoch':epoch,
                    'model_commitment':model_commitment, 'policy_id':policy_id,
                    'message_algorithm':'ECDSA-P256-SHA256', 'financial_authority':False}
        binding, signature = self.s.crypto_registry.sign(agent, DOMAIN, body)
        updated = self.call('bind', agent=agent, binding_hash=digest(binding), epoch=epoch)
        bundle = {'binding':binding, 'signature':signature, 'chain_readback':updated}
        self.verify_binding(agent,bundle)
        return bundle

    def verify_binding(self, agent, bundle):
        try:
            body = bundle['binding']
            identity = self.s.crypto_registry.resolve(agent)
            self.s.crypto_registry.require_active(agent)
            if (set(body)!={'version','agent','did','chain_id','identity_uri','key_id','public_key',
                          'public_key_fingerprint','capability_hash','epoch','model_commitment','policy_id',
                          'message_algorithm','financial_authority'}
                    or body['version']!=1 or body['chain_id']!=101
                    or type(body['epoch']) is not int or not 0<=body['epoch']<2**31
                    or body['identity_uri']!=identity['identity_uri']
                    or body['public_key_fingerprint']!=fingerprint(identity['public_key'])
                    or body['agent'] != agent or body['key_id'] != identity['key_id']
                    or body['public_key'] != identity['public_key']
                    or body['capability_hash'] != identity['capability_hash']
                    or body['message_algorithm'] != 'ECDSA-P256-SHA256'
                    or body['financial_authority'] is not False):
                raise ValueError()
            verify_object(identity['public_key'],DOMAIN,body,bundle['signature'])
            current = self.call('query',agent=agent)
            document = current['document']
            if (current['deactivated'] is not False or current['did'] != body['did']
                    or document['id'] != body['did']
                    or current['contract_address'] != bundle['chain_readback']['contract_address']):
                raise ValueError()
            expected_id = body['did']+'#trusta-p256-'+str(body['epoch'])
            matches = [v for v in document['service'] if v['id'] == expected_id]
            if (len(matches) != 1 or matches[0]['type'] != 'TRUSTA-P256-Binding'
                    or matches[0]['serviceEndpoint'] != 'urn:trusta:anchor:binding:'+digest(body)):
                raise ValueError()
            return current
        except (KeyError, ValueError, TypeError):
            raise Rejected('ANCHOR_WEID_BINDING_INVALID') from None

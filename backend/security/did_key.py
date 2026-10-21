"""Stateless did:key P-256 resolution plus a separate, live Registry capability binding."""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from backend.domain import Rejected
from backend.security.crypto import verify_object

ALPHABET='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
PREFIX=b'\x80\x24'  # unsigned-varint multicodec p256-pub 0x1200
DOMAIN='trusta-did-capability-binding-v1'


def b58encode(value):
    number=int.from_bytes(value,'big');out=''
    while number:
        number,rem=divmod(number,58);out=ALPHABET[rem]+out
    return '1'*(len(value)-len(value.lstrip(b'\0')))+out


def b58decode(value):
    if not isinstance(value,str) or not 1<=len(value)<=80 or any(c not in ALPHABET for c in value):
        raise Rejected('INVALID_DID_KEY')
    number=0
    for char in value: number=number*58+ALPHABET.index(char)
    return b'\0'*(len(value)-len(value.lstrip('1')))+number.to_bytes((number.bit_length()+7)//8,'big')


def from_public_key(pem):
    try:
        key=serialization.load_pem_public_key(pem.encode())
        if not isinstance(key,ec.EllipticCurvePublicKey) or not isinstance(key.curve,ec.SECP256R1): raise ValueError()
        compressed=key.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.CompressedPoint)
        return 'did:key:z'+b58encode(PREFIX+compressed)
    except (ValueError,TypeError,AttributeError):
        raise Rejected('INVALID_DID_KEY') from None


def public_key(did):
    if not isinstance(did,str) or not did.startswith('did:key:z') or len(did)>90:
        raise Rejected('INVALID_DID_KEY')
    raw=b58decode(did[9:])
    if len(raw)!=35 or raw[:2]!=PREFIX or did!='did:key:z'+b58encode(raw):
        raise Rejected('UNSUPPORTED_DID_KEY')
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw[2:])
    except ValueError: raise Rejected('INVALID_DID_KEY') from None


def resolve(did):
    public_key(did) # Reject invalid points before constructing a document.
    method=did+'#'+did[8:]
    return {'@context':['https://www.w3.org/ns/did/v1','https://w3id.org/security/multikey/v1'],
            'id':did,'verificationMethod':[{'id':method,'type':'Multikey','controller':did,'publicKeyMultibase':did[8:]}],
            'authentication':[method],'assertionMethod':[method],'capabilityInvocation':[method],'capabilityDelegation':[method]}


class DIDKeyBinding:
    def __init__(self, registry): self.registry=registry

    def resolve(self, identifier): return resolve(identifier)

    def export(self, agent):
        def body(identity):
            did=from_public_key(identity['public_key'])
            return {'schema_version':1,'agent_id':agent,'identity_uri':identity['identity_uri'],'did':did,
                    'key_id':identity['key_id'],'key_version':identity['key_version'],
                    'capability_hash':identity['capability_hash'],'capabilities':self.registry.capabilities.agent_snapshot(agent),
                    'agent_version':identity['agent_version'],'status':identity['status'],
                    'issued_at':self.registry.now(),'expires_at':self.registry.now()+60000,
                    'authority':'REGISTRY_BOUND_IDENTITY_ONLY'}
        value,signature=self.registry.sign(agent,DOMAIN,body)
        return {'binding':value,'signature':signature,'did_document':resolve(value['did'])}

    def verify(self, bundle):
        try:
            if set(bundle)!={'binding','signature','did_document'}: raise ValueError()
            body=bundle['binding'];agent=body['agent_id']
            with self.registry.lock,self.registry.db.transaction() as conn:
                identity,key=self.registry.resolve_in(agent,body['key_id'],conn)
                expected={'schema_version':1,'agent_id':agent,'identity_uri':identity['identity_uri'],
                          'did':from_public_key(identity['public_key']),'key_id':identity['key_id'],'key_version':identity['key_version'],
                          'capability_hash':identity['capability_hash'],'capabilities':self.registry.capabilities.agent_snapshot(agent),
                          'agent_version':identity['agent_version'],'status':identity['status'],
                          'issued_at':body['issued_at'],'expires_at':body['expires_at'],'authority':'REGISTRY_BOUND_IDENTITY_ONLY'}
                if (body!=expected or type(body['issued_at']) is not int or type(body['expires_at']) is not int
                        or not body['issued_at']<=self.registry.now()<body['expires_at']<=body['issued_at']+60000
                        or bundle['did_document']!=resolve(body['did'])): raise ValueError()
                verify_object(key['public_key'],DOMAIN,body,bundle['signature'])
            return True
        except (ValueError,TypeError,KeyError,Rejected):
            raise Rejected('DID_BINDING_INVALID') from None

"""Actual official WeID key-binding rotation/revocation/restart on new test keys."""
import json
from pathlib import Path
import secrets
from backend.domain import Rejected,digest
from backend.service import TrustaService
from backend.config import Config
from backend.security.local_files import protect_secret
from backend.collaboration.weidentity import WeIdentityLab


def main():
    root=Path('data/anchor-integration/identity-lifecycle');root.mkdir(exist_ok=True)
    secret=root/'seal.key'
    if not secret.exists():secret.write_bytes(secrets.token_bytes(32))
    protect_secret(secret)
    config=Config(db_path=str(root/'identity.sqlite3'),signing_key=secret.read_bytes(),banking_version=5,
                  communication_version=3,strong_auth_secret='isolated-no-financial-writes')
    s=TrustaService(config);identity=WeIdentityLab(s);model=digest({'scope':'IDENTITY_LIFECYCLE_TEST_ONLY'})
    policy=digest({'policy':'IDENTITY_LIFECYCLE_TEST_NOT_M1'})
    first=identity.register_binding('service',epoch=10,model_commitment=model,policy_id=policy)
    s.crypto_registry.rotate('service',0)
    try:identity.verify_binding('service',first)
    except Rejected:stale_rejected=True
    else:raise RuntimeError('old binding survived rotation')
    second=identity.register_binding('service',epoch=11,model_commitment=model,policy_id=policy)
    revoked=identity.call('deactivate',agent='service')
    try:identity.verify_binding('service',second)
    except Rejected:revoked_rejected=True
    else:raise RuntimeError('deactivated WeID accepted')
    reactivated=identity.call('reactivate',agent='service')
    s.crypto_registry.rotate('service',0)
    third=identity.register_binding('service',epoch=12,model_commitment=model,policy_id=policy)
    restarted=TrustaService(config);restored=WeIdentityLab(restarted).verify_binding('service',third)
    restarted.crypto_registry.set_status('service','REVOKED')
    try:WeIdentityLab(restarted).verify_binding('service',third)
    except Rejected:local_revoke_rejected=True
    else:raise RuntimeError('chain reactivation bypassed local revocation')
    result={'scope':'REAL_FISCO_ISOLATED_IDENTITY_LIFECYCLE','stale_binding_rejected':stale_rejected,
        'chain_revocation_rejected':revoked_rejected,'local_revocation_not_bypassed':local_revoke_rejected,
        'new_binding_after_rotation':third,'deactivation':revoked,'reactivation':reactivated,
        'restart_readback':restored,'financial_writes':len(s.db.rows('SELECT * FROM transactions'))}
    (root/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if type(v) in (str,bool,int)}),flush=True)


if __name__=='__main__':main()

"""Interactive local signer enrollment; never signs or broadcasts a transaction."""
import argparse,getpass,json,os,secrets,sys,time,warnings
from pathlib import Path
from eth_account import Account
from backend.domain import Rejected,canonical
from scripts.estimate_sepolia_costs import load_local_environment
from backend.audit_trust.transaction_safety import signing_ready
ROOT=Path(__file__).resolve().parents[1]
EXPECTED_WALLET='0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE'

def hidden_input(prompt):
    # getpass may otherwise warn and fall back to echoed stdin. Fail closed.
    if not sys.stdin.isatty() or not sys.stderr.isatty():raise Rejected('ANCHOR_INTERACTIVE_TERMINAL_REQUIRED')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:return getpass.getpass(prompt)
        except getpass.GetPassWarning:raise Rejected('ANCHOR_HIDDEN_INPUT_UNAVAILABLE') from None

def enroll(secret,password,expected,path):
    """Address binding before writing an encrypted, exclusively created keystore."""
    if not password or len(password)<12:raise Rejected('ANCHOR_KEYSTORE_PASSWORD_TOO_SHORT')
    try:account=Account.from_key(secret)
    except Exception:raise Rejected('ANCHOR_SIGNER_INVALID') from None
    if account.address.lower()!=expected.lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    encrypted=Account.encrypt(account.key,password,kdf='scrypt')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    from backend.security.local_files import protect_secret
    protect_secret(path.parent)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        protect_secret(path)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            fd=None;json.dump(encrypted,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    except Exception:
        if fd is not None:os.close(fd)
        path.unlink(missing_ok=True);raise
    return {'status':'LOCAL_KEYSTORE_ENROLLED','address':account.address,'path':str(path.resolve()),'real_signatures':0,'broadcasts':0}

def verify_control(path,password,expected):
    """Public EIP-191 control proof, never a transaction/permit or RPC call."""
    from eth_account.messages import encode_defunct
    from backend.security.local_files import protect_secret
    protect_secret(Path(path).parent);protect_secret(path)
    try:wallet=Account.from_key(Account.decrypt(json.loads(Path(path).read_text(encoding='utf-8')),password))
    except Exception:raise Rejected('ANCHOR_LOCAL_SIGNER_UNAVAILABLE') from None
    if wallet.address.lower()!=expected.lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    message={'domain':'TRUSTA-SEPOLIA-WALLET-CONTROL-V1','chain_id':11155111,'wallet':expected,
             'purpose':'OFFLINE_CONTROL_PROOF_ONLY_NO_TRANSACTION_OR_FINANCIAL_AUTHORIZATION',
             'nonce':secrets.token_hex(32),'created_at':int(time.time())}
    payload=encode_defunct(text=canonical(message));signed=wallet.sign_message(payload)
    recovered=Account.recover_message(payload,signature=signed.signature)
    if recovered.lower()!=expected.lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    return {'schema_version':1,'message':message,'signature':'0x'+signed.signature.hex().removeprefix('0x'),
            'recovered_address':recovered,'wallet_control_verified':True,'transaction_signatures':0,
            'broadcasts':0,'external_blockchain_verified':False}

def main():
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['status','enroll-keystore','verify-keystore'])
    p.add_argument('--keystore',type=Path);args=p.parse_args();load_local_environment()
    if args.operation=='status':
        print(json.dumps({'signing':signing_ready(),'wallet_public_address':os.getenv('ANCHOR_WALLET_ADDRESS',''),'signatures':0,'broadcasts':0}));return
    expected=os.getenv('ANCHOR_WALLET_ADDRESS','')
    if expected.lower()!=EXPECTED_WALLET.lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    path=args.keystore or Path(os.getenv('ANCHOR_KEYSTORE_PATH') or ROOT/'data/anchor-signing/wallet.json')
    if not path.is_absolute():path=ROOT/path
    if args.operation=='verify-keystore':
        password=hidden_input('Keystore password (hidden, local only): ')
        try:proof=verify_control(path,password,expected)
        finally:password=None
    else:
        # Console input only, never argv, chat, LLM or persisted plaintext.
        secret=hidden_input('Existing Sepolia wallet private key (hidden, local only): ')
        password=hidden_input('New keystore password (12+ characters): ')
        if password!=hidden_input('Repeat password: '):raise Rejected('ANCHOR_KEYSTORE_PASSWORD_MISMATCH')
        try:
            enroll(secret,password,expected,path)
            proof=verify_control(path,password,expected)
        finally:secret=password=None
    output=ROOT/'data/sepolia-finalization/wallet-control-proof.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    from backend.security.local_files import write_secret_atomic
    write_secret_atomic(output,(json.dumps(proof,indent=2)+'\n').encode())
    print(json.dumps({'status':'WALLET_CONTROL_VERIFIED_OFFLINE','address':proof['recovered_address'],
        'keystore_path':str(path),'public_proof_path':str(output),'transaction_signatures':0,'broadcasts':0}))
if __name__=='__main__':
    try:main()
    except Rejected as exc:print(json.dumps({'status':'REJECTED','reason':exc.code}));raise SystemExit(1)
    except (EOFError,KeyboardInterrupt):raise SystemExit(1)
    except Exception:print(json.dumps({'status':'REJECTED','reason':'ANCHOR_OPERATOR_FAILED'}));raise SystemExit(1)

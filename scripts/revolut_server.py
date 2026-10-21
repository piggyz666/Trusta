"""Dedicated local UI for Revolut Sandbox, isolated from the default Virtual Bank."""
import argparse
from pathlib import Path
import secrets
import sys
import threading
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Config
from backend.domain import Rejected
from backend.server import create_server
from backend.service import TrustaService
from backend.security.local_files import protect_secret,write_secret_atomic

def checkpoint_tick(service):
    previous=getattr(service,'checkpoint_worker_status',{'failures':0})
    try:
        service.checkpoints.generate_checkpoint()
        service.checkpoint_worker_status={'status':'ok','failures':previous['failures']}
    except Exception:
        service.checkpoint_worker_status={'status':'degraded','failures':previous['failures']+1}
        if previous.get('status')!='degraded':
            print('CHECKPOINT_WORKER_FAILED; inspect the authenticated proof endpoint.',file=sys.stderr,flush=True)


def build(profile):
    profile=Path(profile).resolve()
    settings=tomllib.loads(profile.read_text(encoding='utf-8'))
    if settings.get('provider')!='revolut' or settings.get('environment')!='sandbox': raise Rejected('BANK_ENVIRONMENT_MISMATCH')
    tokenfile=Path(settings['token_path'])
    if not tokenfile.is_absolute():tokenfile=profile.parent/tokenfile
    directory=tokenfile.resolve().parent/('transfer-service' if settings.get('transfer_profile') else 'readonly-service')
    directory.mkdir(exist_ok=True)
    protect_secret(directory)
    def secret(name,length):
        path=directory/name
        if not path.exists():
            write_secret_atomic(path,secrets.token_bytes(length),create_only=True)
        protect_secret(path)
        if path.stat().st_size!=length:raise Rejected('INVALID_LOCAL_SECRET')
        return path.read_bytes()
    s=TrustaService(Config(db_path=str(directory/'service.sqlite3'),signing_key=secret('signing.key',32),
        communication_version=3,banking_version=5,bank_config_path=str(profile)),recover_workflows=True)
    return s,secret('browser.key',32).hex()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(ROOT/'config/bank.revolut.local.toml'))
    parser.add_argument('--port',type=int,default=8001)
    args=parser.parse_args()
    s,token=build(args.config);server=create_server(s,token,args.port)
    stop=threading.Event()
    def checkpoints():
        while not stop.wait(3):
            checkpoint_tick(s)
    thread=threading.Thread(target=checkpoints,daemon=True);thread.start()
    # Browser token grants only this loopback UI. Never print bank credentials.
    print('TRUSTA — Revolut 官方 Sandbox（测试资金）',flush=True)
    print(f'打开 http://127.0.0.1:{server.server_port}/#token={token}',flush=True)
    try:server.serve_forever()
    finally:stop.set();server.server_close();thread.join(timeout=4)


if __name__=='__main__':main()

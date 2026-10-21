"""AppContainer entry point: public verifier and existing Finance implementation.

No key store, bank adapter, database or generic signer is imported/staged here.
The launch-bound pipe provides the pinned public trust snapshot; packets do not.
"""
import base64
import ctypes as c
import hashlib
import json
import os
import struct
import sys
import time
from types import SimpleNamespace
from backend.domain import canonical, digest
from backend.agents.finance import FinanceAgent


def verify_p256(point, data, signature):
    """Windows CNG verifies P-256; this code only translates DER signatures."""
    raw=base64.b64decode(signature,validate=True)
    if not 8<=len(raw)<=72 or raw[0]!=48 or raw[1]!=len(raw)-2: raise ValueError()
    values=[];offset=2
    for _ in range(2):
        if raw[offset]!=2: raise ValueError()
        size=raw[offset+1];number=raw[offset+2:offset+2+size];offset+=2+size
        if not number or number[0]&128 or (len(number)>1 and number[0]==0 and not number[1]&128): raise ValueError()
        value=int.from_bytes(number,'big')
        if not 0<value<2**256: raise ValueError()
        values.append(value.to_bytes(32,'big'))
    if offset!=len(raw): raise ValueError()
    point=bytes.fromhex(point)
    if len(point)!=65 or point[0]!=4: raise ValueError()
    blob=struct.pack('<II',0x31534345,32)+point[1:]
    dll=c.WinDLL('bcrypt');alg=c.c_void_p();key=c.c_void_p()
    dll.BCryptOpenAlgorithmProvider.argtypes=[c.POINTER(c.c_void_p),c.c_wchar_p,c.c_wchar_p,c.c_ulong]
    dll.BCryptImportKeyPair.argtypes=[c.c_void_p,c.c_void_p,c.c_wchar_p,c.POINTER(c.c_void_p),c.c_void_p,c.c_ulong,c.c_ulong]
    dll.BCryptVerifySignature.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p,c.c_ulong,c.c_void_p,c.c_ulong,c.c_ulong]
    dll.BCryptDestroyKey.argtypes=[c.c_void_p];dll.BCryptCloseAlgorithmProvider.argtypes=[c.c_void_p,c.c_ulong]
    try:
        if dll.BCryptOpenAlgorithmProvider(c.byref(alg),'ECDSA_P256',None,0): raise ValueError()
        if dll.BCryptImportKeyPair(alg,None,'ECCPUBLICBLOB',c.byref(key),blob,len(blob),0): raise ValueError()
        hashed=hashlib.sha256(data).digest();sig=b''.join(values)
        if dll.BCryptVerifySignature(key,None,hashed,len(hashed),sig,len(sig),0): raise ValueError()
    finally:
        if key: dll.BCryptDestroyKey(key)
        if alg: dll.BCryptCloseAlgorithmProvider(alg,0)


def verify(packet, trust, nonces):
    message=packet['message'];payload=packet['payload']
    from_fields={'schema_version','canonicalization','message_id','task_id','step_id','sender_agent','receiver_agent',
        'sender_key_id','sender_identity','message_type','payload_ref','payload_hash','payload_version',
        'capability_snapshot_hash','issued_at','expires_at','nonce','signature_algorithm','signature'}
    if set(message)!=from_fields or message['schema_version']!=3 or message['canonicalization']!='TRUSTA-JSON-v1' or message['signature_algorithm']!='ECDSA-P256-SHA256': raise ValueError()
    if message['sender_agent']!='supervisor' or message['receiver_agent']!='finance' or message['message_type']!='REQUEST': raise ValueError()
    key=trust[message['sender_key_id']]
    if key['agent_id']!='supervisor' or key['identity_uri']!=message['sender_identity'] or key['capability_hash']!=message['capability_snapshot_hash']: raise ValueError()
    now=int(time.time()*1000)
    if key['verify_until'] is not None and key['verify_until']<=now: raise ValueError()
    if any(type(message[k]) is not int for k in ('payload_version','issued_at','expires_at')): raise ValueError()
    if not now-300000<=message['issued_at']<=now+30000 or not message['issued_at']<message['expires_at']<=message['issued_at']+300000 or message['expires_at']<=now: raise ValueError()
    if digest(payload)!=message['payload_hash'] or message['nonce'] in nonces: raise ValueError()
    if set(payload)!={'request_id','task_id','step_id','revision','capability','values','parent_hashes'}: raise ValueError()
    if payload['task_id']!=message['task_id'] or payload['step_id']!=message['step_id'] or payload['revision']!=message['payload_version']: raise ValueError()
    if message['payload_ref']!='handoff:'+payload['request_id']: raise ValueError()
    verify_p256(key['point'],b'TRUSTA-AGENT-MESSAGE-V3\0'+canonical({k:v for k,v in message.items() if k!='signature'}).encode(),message['signature'])
    nonces.add(message['nonce'])
    return payload


def main():
    sys.stdout.reconfigure(encoding='utf-8',write_through=True)
    trust=None;nonces=set();finance=None
    for line in sys.stdin.buffer:
        try:
            if len(line)>1048576: raise ValueError()
            packet=json.loads(line)
            if trust is None:
                if set(packet)!={'bootstrap_public_keys','finance_config'}: raise ValueError()
                trust=packet['bootstrap_public_keys'];cfg=packet['finance_config']
                finance=FinanceAgent(None,SimpleNamespace(**cfg),cfg['currencies'],cfg['default_currency'])
                answer={'ready':True,'pid':os.getpid()}
            elif packet.get('operation')=='probe':
                # Deliberately try the prohibited accesses in the actual child.
                results={}
                for name,path in packet['paths'].items():
                    try:
                        with open(path,'rb') as stream: stream.read(1)
                        results[name]='ACCESS_ALLOWED'
                    except OSError: results[name]='ACCESS_DENIED'
                try:
                    __import__('backend.banking.adapter');results['bank_adapter_import']='ACCESS_ALLOWED'
                except ImportError: results['bank_adapter_import']='ACCESS_DENIED'
                results['secret_env_present']=any(name in os.environ for name in ('GEMINI_API_KEY','DEEPSEEK_API_KEY','TRUSTA_API_TOKEN'))
                if 'write_path' in packet:
                    try:
                        with open(packet['write_path'],'xb') as stream: stream.write(b'probe')
                        results['runtime_write']='ACCESS_ALLOWED'
                    except OSError: results['runtime_write']='ACCESS_DENIED'
                if 'parent_pid' in packet:
                    kernel=c.WinDLL('kernel32',use_last_error=True)
                    kernel.OpenProcess.argtypes=[c.c_ulong,c.c_int,c.c_ulong];kernel.OpenProcess.restype=c.c_void_p
                    kernel.CloseHandle.argtypes=[c.c_void_p]
                    handle=kernel.OpenProcess(0x10,False,packet['parent_pid'])
                    results['parent_memory']='ACCESS_ALLOWED' if handle else 'ACCESS_DENIED'
                    if handle: kernel.CloseHandle(handle)
                if 'network_port' in packet:
                    import socket
                    with socket.socket() as sock:
                        sock.settimeout(1)
                        try: results['network_connect']='ACCESS_ALLOWED' if sock.connect_ex(('127.0.0.1',packet['network_port']))==0 else 'ACCESS_DENIED'
                        except OSError:results['network_connect']='ACCESS_DENIED'
                answer={'probe':results,'pid':os.getpid()}
            else:
                payload=verify(packet,trust,nonces)
                values=payload['values'];cap=payload['capability']
                if cap=='finance.summary':
                    result=finance.summarize(values['records'])
                elif cap=='finance.anomalies':
                    result=finance.anomalies(values['records'])
                elif cap in ('accounts.read','balances.read','transactions.read'):
                    # The response asks for a narrow runtime-bound read. The
                    # broker supplies bank data only after Gateway verification.
                    answer={'read_request':{'request_id':payload['request_id'],'capability':cap,'values':values}}
                    print(json.dumps(answer,ensure_ascii=False),flush=True)
                    supplied=json.loads(sys.stdin.buffer.readline(1048577))
                    if set(supplied)!={'bank_read','request_id'} or supplied['request_id']!=payload['request_id']: raise ValueError()
                    result=supplied['bank_read']
                else: raise ValueError()
                answer={'request_id':payload['request_id'],'result':result,'result_hash':digest(result),'pid':os.getpid()}
        except Exception:
            answer={'error':'AGENT_PACKET_REJECTED','pid':os.getpid()}
        print(json.dumps(answer,ensure_ascii=False),flush=True)


if __name__=='__main__': main()

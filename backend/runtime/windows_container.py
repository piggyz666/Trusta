"""No-capability AppContainer worker, with only two inherited IPC handles.

The broker remains trusted. This boundary isolates the agent, not administrators,
the kernel, or the broker. No unrestricted subprocess fallback is permitted.
"""
import ctypes as c
from ctypes import wintypes as w
import hashlib
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from backend.domain import Rejected


def stage_runtime(directory):
    root=Path(directory).resolve()
    repository=Path(__file__).resolve().parents[2]
    if root==repository or repository in root.parents or len(root.parts)<4:
        raise Rejected('AGENT_RUNTIME_LOCATION_DENIED')
    root.mkdir(parents=True,exist_ok=True)
    source=Path(sys.executable).parent
    if os.name!='nt' or sys.version_info[:2]!=(3,13) or not (source/'Lib').is_dir():
        raise Rejected('AGENT_ISOLATION_PLATFORM_UNSUPPORTED')
    for file in [Path(sys.executable),*source.glob('*.dll')]:
        target=root/file.name
        if not target.exists(): shutil.copy2(file,target)
    for name in ('Lib','DLLs'):
        if not (root/name).exists():
            shutil.copytree(source/name,root/name,ignore=shutil.ignore_patterns(
                'site-packages','__pycache__','test','tests','idlelib','tkinter','ensurepip'))
    package=root/'backend'
    for folder in ('','agents','runtime'):
        (package/folder).mkdir(exist_ok=True)
        if not (package/folder/'__init__.py').exists():
            (package/folder/'__init__.py').write_text('',encoding='utf-8')
    for relative in ('backend/domain.py','backend/business.py','backend/runtime/banking_plan.py',
                     'backend/agents/finance.py','backend/runtime/finance_worker.py'):
        target=root/relative;data=(repository/relative).read_bytes()
        if not target.exists() or target.read_bytes()!=data:
            temporary=target.with_suffix('.staging-'+str(os.getpid()))
            temporary.write_bytes(data);temporary.replace(target)
    if not (root/'python313._pth').exists():
        (root/'python313._pth').write_text('Lib\nDLLs\n.\n',encoding='ascii')
    return root


class STARTUPINFO(c.Structure):
    _fields_=[('cb',w.DWORD),('lpReserved',w.LPWSTR),('lpDesktop',w.LPWSTR),('lpTitle',w.LPWSTR),
        *[(name,w.DWORD) for name in ('dwX','dwY','dwXSize','dwYSize','dwXCountChars','dwYCountChars','dwFillAttribute','dwFlags')],
        ('wShowWindow',w.WORD),('cbReserved2',w.WORD),('lpReserved2',c.c_void_p),
        ('hStdInput',w.HANDLE),('hStdOutput',w.HANDLE),('hStdError',w.HANDLE)]


class STARTUPINFOEX(c.Structure):
    _fields_=[('StartupInfo',STARTUPINFO),('lpAttributeList',c.c_void_p)]


class PROCESSINFO(c.Structure):
    _fields_=[('hProcess',w.HANDLE),('hThread',w.HANDLE),('dwProcessId',w.DWORD),('dwThreadId',w.DWORD)]


class SECURITYCAPS(c.Structure):
    _fields_=[('AppContainerSid',c.c_void_p),('Capabilities',c.c_void_p),('CapabilityCount',w.DWORD),('Reserved',w.DWORD)]


class SECURITYATTR(c.Structure):
    _fields_=[('nLength',w.DWORD),('lpSecurityDescriptor',c.c_void_p),('bInheritHandle',w.BOOL)]


class JOBLIMIT(c.Structure):
    _fields_=[('PerProcessUserTimeLimit',c.c_longlong),('PerJobUserTimeLimit',c.c_longlong),
        ('LimitFlags',w.DWORD),('MinimumWorkingSetSize',c.c_size_t),('MaximumWorkingSetSize',c.c_size_t),
        ('ActiveProcessLimit',w.DWORD),('Affinity',c.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]


class JOBEXTENDED(c.Structure):
    _fields_=[('BasicLimitInformation',JOBLIMIT),('IoInfo',c.c_ulonglong*6),
        ('ProcessMemoryLimit',c.c_size_t),('JobMemoryLimit',c.c_size_t),
        ('PeakProcessMemoryUsed',c.c_size_t),('PeakJobMemoryUsed',c.c_size_t)]


class ContainerProcess:
    def __init__(self,directory,timeout=20):
        try:
            self._initialize(directory,timeout)
        except Exception:
            self.close()
            raise

    def _initialize(self,directory,timeout):
        if os.name!='nt': raise Rejected('AGENT_ISOLATION_PLATFORM_UNSUPPORTED')
        self.root=stage_runtime(directory);self.timeout=timeout;self.handles=[]
        k=c.WinDLL('kernel32',use_last_error=True);self.k=k
        user=c.WinDLL('userenv',use_last_error=True)
        adv=c.WinDLL('advapi32',use_last_error=True)
        user.CreateAppContainerProfile.argtypes=[w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,c.c_void_p,w.DWORD,c.POINTER(c.c_void_p)]
        user.DeriveAppContainerSidFromAppContainerName.argtypes=[w.LPCWSTR,c.POINTER(c.c_void_p)]
        k.LocalFree.argtypes=[c.c_void_p];k.CloseHandle.argtypes=[w.HANDLE]
        k.CreatePipe.argtypes=[c.POINTER(w.HANDLE),c.POINTER(w.HANDLE),c.POINTER(SECURITYATTR),w.DWORD]
        k.SetHandleInformation.argtypes=[w.HANDLE,w.DWORD,w.DWORD]
        k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(SECURITYATTR),w.DWORD,w.DWORD,w.HANDLE]
        k.CreateFileW.restype=w.HANDLE
        k.InitializeProcThreadAttributeList.argtypes=[c.c_void_p,w.DWORD,w.DWORD,c.POINTER(c.c_size_t)]
        k.UpdateProcThreadAttribute.argtypes=[c.c_void_p,w.DWORD,c.c_size_t,c.c_void_p,c.c_size_t,c.c_void_p,c.c_void_p]
        k.DeleteProcThreadAttributeList.argtypes=[c.c_void_p]
        k.CreateProcessW.argtypes=[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(STARTUPINFOEX),c.POINTER(PROCESSINFO)]
        k.ReadFile.argtypes=[w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p]
        k.WriteFile.argtypes=k.ReadFile.argtypes
        k.TerminateProcess.argtypes=[w.HANDLE,w.UINT];k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
        k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
        k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        k.ResumeThread.argtypes=[w.HANDLE]
        adv.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
        adv.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
        adv.ConvertSidToStringSidW.argtypes=[c.c_void_p,c.POINTER(w.LPWSTR)]
        sid=c.c_void_p();name='TRUSTA.Finance.'+hashlib.sha256(str(self.root).encode()).hexdigest()[:20]
        hr=user.CreateAppContainerProfile(name,'TRUSTA Finance','Read-only Finance worker',None,0,c.byref(sid))
        if hr and user.DeriveAppContainerSidFromAppContainerName(name,c.byref(sid)):
            raise Rejected('AGENT_CONTAINER_PROFILE_FAILED')
        text=w.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid,c.byref(text)): raise Rejected('AGENT_CONTAINER_SID_FAILED')
        sid_string=text.value;k.LocalFree(text)
        # Only this dedicated runtime receives the container ACE. Never grant
        # project/config/key/DB directories. Runtime is read+execute, not writable.
        grant=subprocess.run(['icacls',str(self.root),'/inheritance:r','/grant:r',
            f'{getpass.getuser()}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F',
            f'*{sid_string}:(OI)(CI)RX','/Q'],capture_output=True,timeout=30)
        if grant.returncode: raise Rejected('AGENT_CONTAINER_ACL_FAILED')
        grant=subprocess.run(['icacls',str(self.root/'*'),'/reset','/T','/Q'],capture_output=True,timeout=30)
        if grant.returncode: raise Rejected('AGENT_CONTAINER_ACL_FAILED')
        # No ancestor ACL changes. CPython may emit a harmless location warning;
        # stderr is separated from the strict IPC stream and never exported.
        attr=SECURITYATTR(c.sizeof(SECURITYATTR),None,True)
        read_in,write_in,read_out,write_out=(w.HANDLE() for _ in range(4))
        if not k.CreatePipe(c.byref(read_in),c.byref(write_in),c.byref(attr),0) or not k.CreatePipe(c.byref(read_out),c.byref(write_out),c.byref(attr),0):
            raise Rejected('AGENT_PIPE_FAILED')
        stderr=k.CreateFileW('NUL',0x40000000,3,c.byref(attr),3,0,None)
        if stderr==c.c_void_p(-1).value: raise Rejected('AGENT_STDERR_FAILED')
        self.handles=[read_in,write_in,read_out,write_out,stderr]
        k.SetHandleInformation(write_in,1,0);k.SetHandleInformation(read_out,1,0)
        size=c.c_size_t();k.InitializeProcThreadAttributeList(None,2,0,c.byref(size))
        buffer=c.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(buffer,2,0,c.byref(size)): raise Rejected('AGENT_ATTRIBUTES_FAILED')
        caps=SECURITYCAPS(sid,None,0,0);handles=(w.HANDLE*3)(read_in.value,write_out.value,stderr)
        for attribute,value in ((0x20009,caps),(0x20002,handles)):
            if not k.UpdateProcThreadAttribute(buffer,0,attribute,c.byref(value),c.sizeof(value),None,None):
                raise Rejected('AGENT_ATTRIBUTES_FAILED')
        start=STARTUPINFOEX();start.StartupInfo.cb=c.sizeof(start);start.StartupInfo.dwFlags=0x100
        start.StartupInfo.hStdInput=read_in;start.StartupInfo.hStdOutput=write_out;start.StartupInfo.hStdError=stderr
        start.lpAttributeList=c.cast(buffer,c.c_void_p)
        process=PROCESSINFO()
        executable=self.root/Path(sys.executable).name
        command=c.create_unicode_buffer(subprocess.list2cmdline([str(executable),'-I','-S','-u','-m','backend.runtime.finance_worker']))
        environment={name:os.environ[name] for name in ('SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA','TEMP','TMP') if name in os.environ}
        environment.update(PYTHONIOENCODING='utf-8',PATH=str(self.root)+os.pathsep+os.environ['SystemRoot']+'\\System32')
        env=c.create_unicode_buffer('\0'.join(name+'='+value for name,value in sorted(environment.items(),key=lambda item:item[0].upper()))+'\0\0')
        ok=k.CreateProcessW(str(executable),command,None,None,True,0x80000|0x400|0x8000000|4,env,str(self.root),c.byref(start),c.byref(process))
        error=c.get_last_error();k.DeleteProcThreadAttributeList(buffer)
        c.WinDLL('advapi32').FreeSid(sid)
        if not ok:
            self.close();raise Rejected('AGENT_CONTAINER_LAUNCH_FAILED',str(error))
        self.process=process.hProcess;self.pid=process.dwProcessId
        self.job=k.CreateJobObjectW(None,None)
        limits=JOBEXTENDED();limits.BasicLimitInformation.LimitFlags=0x2000|0x100|0x8
        limits.BasicLimitInformation.ActiveProcessLimit=1;limits.ProcessMemoryLimit=256*1024*1024
        if not self.job or not k.SetInformationJobObject(self.job,9,c.byref(limits),c.sizeof(limits)) or not k.AssignProcessToJobObject(self.job,self.process):
            k.CloseHandle(process.hThread);self.close();raise Rejected('AGENT_JOB_LIMIT_FAILED')
        k.ResumeThread(process.hThread)
        k.CloseHandle(process.hThread);k.CloseHandle(read_in);k.CloseHandle(write_out);k.CloseHandle(stderr)
        self.handles=[write_in,read_out];self.input,self.output=write_in,read_out
        token=w.HANDLE();flag=w.DWORD();length=w.DWORD()
        if not adv.OpenProcessToken(self.process,8,c.byref(token)):
            self.close();raise Rejected('AGENT_CONTAINER_TOKEN_FAILED')
        try: verified=bool(adv.GetTokenInformation(token,29,c.byref(flag),4,c.byref(length))) and flag.value==1
        finally: k.CloseHandle(token)
        if not verified:
            self.close();raise Rejected('AGENT_CONTAINER_TOKEN_FAILED')
        self.security={'classification':'PROCESS_ISOLATION_VERIFIED','agent':'finance',
            'pid':self.pid,'parent_pid':os.getpid(),'appcontainer_sid':sid_string,
            'os_token_appcontainer':True,'network_capabilities':[],
            'private_keys_in_child':False,'broker_trust':'LOCAL_HOST_TRUST'}

    def exchange(self,request):
        raw=(json.dumps(request,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
        if len(raw)>1048576: raise Rejected('AGENT_MESSAGE_TOO_LARGE')
        result=[]
        def receive():
            data=bytearray();buf=c.create_string_buffer(4096);count=w.DWORD()
            try:
                # Bound the whole exchange, including a pipe write to a child
                # that has stopped reading. A response-only timeout is unsafe.
                if not self.k.WriteFile(self.input,raw,len(raw),c.byref(count),None) or count.value!=len(raw):
                    result.append(None);return
                while len(data)<=1048576:
                    if not self.k.ReadFile(self.output,buf,len(buf),c.byref(count),None) or not count.value: break
                    data.extend(buf.raw[:count.value])
                    if b'\n' in data: break
                result.append(json.loads(bytes(data).decode('utf-8')))
            except Exception:
                self.diagnostic=bytes(data).decode('utf-8',errors='replace')[:1000]
                result.append(None)
        thread=threading.Thread(target=receive,daemon=True);thread.start();thread.join(self.timeout)
        if thread.is_alive() or not result or not isinstance(result[0],dict):
            self.close();thread.join(1);raise Rejected('AGENT_PROCESS_TIMEOUT_OR_PROTOCOL')
        return result[0]

    def close(self):
        if getattr(self,'process',None):
            self.k.TerminateProcess(self.process,1);self.k.WaitForSingleObject(self.process,2000)
            self.k.CloseHandle(self.process);self.process=None
        if getattr(self,'job',None): self.k.CloseHandle(self.job);self.job=None
        for handle in getattr(self,'handles',[]): self.k.CloseHandle(handle)
        self.handles=[]

    def __enter__(self): return self
    def __exit__(self,*args): self.close()

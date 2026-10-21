"""Protect host-local secrets with native ACLs, without executable/PATH lookup."""
import os
import secrets
from pathlib import Path
from backend.domain import Rejected

def protect_secret(path):
    path=Path(path)
    if path.is_symlink() or (os.name=='nt' and path.is_junction()):raise Rejected('SECRET_PATH_LINK_DENIED')
    if not path.exists():raise Rejected('SECRET_PATH_NOT_FOUND')
    if os.name!='nt':
        os.chmod(path,0o700 if path.is_dir() else 0o600);return
    import ctypes as c
    from ctypes import wintypes as w
    k=c.WinDLL('kernel32',use_last_error=True);a=c.WinDLL('advapi32',use_last_error=True)
    k.GetCurrentProcess.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE];k.LocalFree.argtypes=[c.c_void_p]
    a.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
    a.GetTokenInformation.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)]
    a.ConvertSidToStringSidW.argtypes=[c.c_void_p,c.POINTER(w.LPWSTR)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)]
    a.GetSecurityDescriptorDacl.argtypes=[c.c_void_p,c.POINTER(w.BOOL),c.POINTER(c.c_void_p),c.POINTER(w.BOOL)]
    a.SetNamedSecurityInfoW.argtypes=[w.LPWSTR,c.c_int,w.DWORD,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p]
    a.SetNamedSecurityInfoW.restype=w.DWORD
    token=w.HANDLE();sid_text=w.LPWSTR();descriptor=c.c_void_p()
    try:
        if not a.OpenProcessToken(k.GetCurrentProcess(),8,c.byref(token)):raise OSError()
        size=w.DWORD();a.GetTokenInformation(token,1,None,0,c.byref(size))
        buffer=c.create_string_buffer(size.value)
        if not a.GetTokenInformation(token,1,buffer,size,c.byref(size)):raise OSError()
        sid=c.cast(buffer,c.POINTER(c.c_void_p))[0]
        if not a.ConvertSidToStringSidW(sid,c.byref(sid_text)):raise OSError()
        flags='OICI' if path.is_dir() else ''
        sddl='D:P'+''.join(f'(A;{flags};FA;;;{principal})' for principal in ('SY','BA',sid_text.value))
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(descriptor),None):raise OSError()
        present=w.BOOL();defaulted=w.BOOL();dacl=c.c_void_p()
        if not a.GetSecurityDescriptorDacl(descriptor,c.byref(present),c.byref(dacl),c.byref(defaulted)):raise OSError()
        if a.SetNamedSecurityInfoW(str(path.resolve()),1,0x80000004,None,None,dacl,None):raise OSError()
    except Exception:
        raise Rejected('SECRET_PERMISSION_FAILED') from None
    finally:
        if token:k.CloseHandle(token)
        if sid_text:k.LocalFree(c.cast(sid_text,c.c_void_p))
        if descriptor:k.LocalFree(descriptor)

def write_secret_atomic(path, data, *, create_only=False):
    """Publish complete secret bytes only after applying a private ACL and fsync."""
    path=Path(path)
    if path.is_symlink() or (os.name=='nt' and path.is_junction()):raise Rejected('SECRET_PATH_LINK_DENIED')
    temporary=path.with_name(path.name+'.'+secrets.token_hex(12)+'.tmp')
    try:
        with temporary.open('xb') as stream:
            protect_secret(temporary)
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        if create_only:
            try:os.link(temporary,path)
            except FileExistsError:pass
        else:temporary.replace(path)
        protect_secret(path)
    finally:
        temporary.unlink(missing_ok=True)

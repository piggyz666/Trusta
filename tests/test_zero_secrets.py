"""Host-local secret permissions and atomic publication; generated keys only."""
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from backend.domain import Rejected
from backend.security.local_files import protect_secret,write_secret_atomic
from backend.security.crypto import LocalKeyStore,sign_object,verify_object

def private_acl(path):
    if os.name!='nt':return os.stat(path).st_mode & 0o077 == 0
    import ctypes as c
    from ctypes import wintypes as w
    a=c.WinDLL('advapi32',use_last_error=True);k=c.WinDLL('kernel32')
    a.GetNamedSecurityInfoW.argtypes=[w.LPCWSTR,c.c_int,w.DWORD,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.POINTER(c.c_void_p)]
    a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[c.c_void_p,w.DWORD,w.DWORD,c.POINTER(w.LPWSTR),c.POINTER(w.DWORD)]
    k.LocalFree.argtypes=[c.c_void_p]
    sd=c.c_void_p();text=w.LPWSTR()
    try:
        if a.GetNamedSecurityInfoW(str(path),1,4,None,None,None,None,c.byref(sd)):raise OSError()
        if not a.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd,1,4,c.byref(text),None):raise OSError()
        acl=text.value
        return acl.startswith('D:P') and all(principal not in acl for principal in (';;;BU)',';;;AU)',';;;WD)')) and ';ID;' not in acl
    finally:
        if text:k.LocalFree(c.cast(text,c.c_void_p))
        if sd:k.LocalFree(sd)

class SecretBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_existing_inherited_secret_is_restricted_without_content_change(self):
        p=self.root/'key';p.write_bytes(b'synthetic-secret');protect_secret(p)
        self.assertTrue(private_acl(p));self.assertEqual(p.read_bytes(),b'synthetic-secret')
    def test_atomic_first_start_race_publishes_one_complete_root(self):
        p=self.root/'signing.key'
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda n:write_secret_atomic(p,bytes([n])*32,create_only=True),range(8)))
        value=p.read_bytes();self.assertEqual(len(value),32);self.assertEqual(len(set(value)),1)
        self.assertTrue(private_acl(p));self.assertEqual(list(self.root.glob('*.tmp')),[])
    def test_failed_write_never_replaces_existing_secret(self):
        from unittest.mock import patch
        p=self.root/'key';write_secret_atomic(p,b'old')
        with patch('backend.security.local_files.os.fsync',side_effect=OSError('disk')):
            with self.assertRaises(OSError):write_secret_atomic(p,b'new')
        self.assertEqual(p.read_bytes(),b'old');self.assertEqual(list(self.root.glob('*.tmp')),[])
    def test_encrypted_key_reload_and_signature_remain_valid(self):
        directory=self.root/'private';kid='key-'+str(uuid.uuid4());root=b'k'*32
        store=LocalKeyStore(directory,root);pem=store.generate(kid)
        key=LocalKeyStore(directory,root).load({'key_id':kid,'public_key':pem})
        verify_object(pem,'zero',{'ok':True},sign_object(key,'zero',{'ok':True}))
        self.assertTrue(private_acl(directory));self.assertTrue(private_acl(store.path(kid)))
    def test_link_secret_is_denied(self):
        from unittest.mock import patch
        p=self.root/'key';p.write_bytes(b'old')
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(Rejected):protect_secret(p)
            with self.assertRaises(Rejected):write_secret_atomic(p,b'new')
        self.assertEqual(p.read_bytes(),b'old')

    def test_database_parent_and_existing_file_are_private_with_wal_usable(self):
        from backend.persistence import Database
        p=self.root/'store'/'state.sqlite3';p.parent.mkdir();p.touch()
        db=Database(p)
        self.assertTrue(private_acl(p.parent));self.assertTrue(private_acl(p))
        conn=db.connect()
        try:
            conn.execute("INSERT INTO customers VALUES('local','synthetic')");conn.commit()
            self.assertTrue(Path(str(p)+'-wal').exists())
            self.assertEqual(db.one('SELECT id FROM customers')['id'],'local')
        finally:conn.close()

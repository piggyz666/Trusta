"""Database-scoped graph ownership. No time-based takeover of financial work."""
import json
import os
import threading
from contextlib import contextmanager
from backend.domain import Rejected,uid


def process_alive(pid):
    """Read-only process query; uncertainty is never permission for takeover."""
    if pid==os.getpid(): return True
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        api.OpenProcess.restype=wintypes.HANDLE
        api.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=api.OpenProcess(0x1000,False,pid)
        if not handle: return ctypes.get_last_error()!=87
        try:
            code=wintypes.DWORD()
            return not api.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally: api.CloseHandle(handle)
    try: os.kill(pid,0);return True
    except ProcessLookupError: return False
    except PermissionError: return True


class GraphClaims:
    def __init__(self,service):
        self.s=service
        self._held=threading.local()
        conn=service.db.connect()
        try: conn.execute('CREATE TABLE IF NOT EXISTS graph_worker_claims(task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL)')
        finally: conn.close()

    @contextmanager
    def hold(self,task_id, *, reentrant=False, recover_metadata=False):
        held: set[str]=getattr(self._held,'tasks',set())
        if reentrant and task_id in held:
            yield
            return
        owner=uid()
        with self.s.db.transaction() as conn:
            prior=conn.execute('SELECT pid FROM graph_worker_claims WHERE task_id=?',(task_id,)).fetchone()
            if prior:
                if recover_metadata and task_id.startswith(('split:','reconcile:','anchor-job:')) and not process_alive(prior['pid']):
                    conn.execute('DELETE FROM graph_worker_claims WHERE task_id=?',(task_id,))
                    event='SPLIT_ABANDONED_CLAIM_RELEASED' if task_id.startswith('split:') else 'METADATA_ABANDONED_CLAIM_RELEASED'
                    self.s.audit.append_in(conn,None,None,event,'recovery',{'claim':task_id,'financial_writes':0})
                else:raise Rejected('GRAPH_WORKER_BUSY')
            conn.execute('INSERT INTO graph_worker_claims VALUES(?,?,?)',(task_id,owner,os.getpid()))
        self._held.tasks=held|{task_id}
        try: yield owner
        finally:
            self._held.tasks=held
            with self.s.db.transaction() as conn:
                conn.execute('DELETE FROM graph_worker_claims WHERE task_id=? AND owner=?',(task_id,owner))

    def live(self,task_id):
        row=self.s.db.one('SELECT pid FROM graph_worker_claims WHERE task_id=?',(task_id,))
        return bool(row and process_alive(row['pid']))

    def release_terminal(self,task_id,user_id):
        # Administrative recovery only after Core's conservative recovery has
        # made the task terminal. Never auto-takeover an EXECUTING task on TTL.
        task=self.s._owned_task(task_id,user_id)
        if task['status'] not in {'FAILED','BLOCKED','CANCELLED','MANUAL_REVIEW','COMPLETED'}:
            raise Rejected('GRAPH_CLAIM_RECOVERY_DENIED')
        with self.s.db.transaction() as conn:
            conn.execute('DELETE FROM graph_worker_claims WHERE task_id=?',(task_id,))
            self.s.audit.append_in(conn,task_id,None,'GRAPH_TERMINAL_CLAIM_RELEASED','runtime',{'status':task['status']})

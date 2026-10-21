"""Per-provider-process rolling request budget; never retries a write."""
from collections import deque
import threading,time
from backend.bank_sdk.contracts import BankError
class BankRateLimiter:
    def __init__(self,per_second,clock=time.monotonic,sleep=time.sleep):
        if type(per_second) is not int or not 1<=per_second<=100:raise BankError('INVALID_BANK_RATE_LIMIT')
        self.limit,self.clock,self.sleep=per_second,clock,sleep;self.lock=threading.Lock();self.history=deque()
    def acquire(self,deadline=None):
        with self.lock:
            while True:
                now=self.clock()
                while self.history and self.history[0]<=now-1:self.history.popleft()
                if len(self.history)<self.limit:
                    if deadline is not None and time.time()>=deadline:raise BankError('BANK_DEADLINE_EXPIRED')
                    self.history.append(now);return
                wait=max(0.001,self.history[0]+1.001-now)
                if deadline is not None and time.time()+wait>=deadline:raise BankError('BANK_DEADLINE_EXPIRED')
                self.sleep(wait)

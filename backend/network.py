"""Bounded HTTP body reader shared by transports, without changing proxy policy."""
import time


class ResponseTooLarge(ValueError):
    pass


def read_bounded(response, limit, deadline):
    lengths=response.headers.get_all('Content-Length') if hasattr(response.headers,'get_all') else None
    if lengths and len(lengths)!=1: raise ValueError('ambiguous response length')
    length=response.headers.get('Content-Length')
    expected=None if length is None else int(length)
    if expected is not None:
        if expected<0: raise ValueError('invalid response length')
        if expected>limit: raise ResponseTooLarge()
    data=bytearray()
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0: raise TimeoutError()
        # HTTPResponse.read1 performs at most one underlying socket read. A
        # per-read timeout alone permits an endless stream of small chunks.
        raw=getattr(getattr(response,'fp',None),'raw',None)
        sock=getattr(raw,'_sock',None)
        if sock is not None: sock.settimeout(remaining)
        reader=getattr(response,'read1',response.read)
        chunk=reader(min(16384,limit+1-len(data)))
        data.extend(chunk)
        if len(data)>limit: raise ResponseTooLarge()
        if not chunk: break
    if time.monotonic()>deadline: raise TimeoutError()
    if expected is not None and len(data)!=expected: raise ValueError('truncated response')
    return bytes(data)

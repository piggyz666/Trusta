from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import threading
import socket
from urllib.parse import urlparse, parse_qs
from backend.config import Config
from backend.domain import Rejected, canonical
from backend.llm.contracts import InferenceError
from backend.service import TrustaService
from backend.security.http_headers import security_headers


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
DIST = FRONTEND / "dist"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png", ".ico": "image/x-icon",
         ".json": "application/json; charset=utf-8", ".webmanifest": "application/manifest+json",
         ".mp4": "video/mp4", ".webm": "video/webm", ".jpg": "image/jpeg", ".webp": "image/webp"}
SPA_ROUTES = {"/funds","/credit-account","/todos","/gold", "/", "/overview", "/bills", "/wealth", "/credit", "/life", "/plans", "/features", "/settings", "/deposits", "/gold-savings", "/exchange", "/support"}


def serve_static(path):
    """The web / H5 app (frontend/dist, built from frontend-app) and the legacy single-page console at /legacy.
    Only files that exist under the two folders are served; anything else falls through to the API (and its auth)."""
    legacy = {"/legacy": "index.html", "/legacy/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
    if path in legacy:
        file = FRONTEND / legacy[path]
        return file.read_bytes(), TYPES[file.suffix]
    index = DIST / "index.html"
    if not index.exists():
        return ((FRONTEND / "index.html").read_bytes(), TYPES[".html"]) if path == "/" else None
    if path.rstrip("/") in SPA_ROUTES or path == "/":
        return index.read_bytes(), TYPES[".html"]
    if path.startswith("/api/") or ".." in path:
        return None
    file = (DIST / path.lstrip("/")).resolve()
    if file.is_file() and DIST.resolve() in file.parents and file.suffix in TYPES:
        return file.read_bytes(), TYPES[file.suffix]
    return None


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,handler,max_workers):
        self._capacity=threading.BoundedSemaphore(max_workers)
        self._count_lock=threading.Lock();self.worker_count=0;self.request_errors=0
        super().__init__(address,handler)

    def process_request(self,request,address):
        if not self._capacity.acquire(blocking=False):
            try:
                request.settimeout(.1)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError: pass
            finally: self.shutdown_request(request)
            return
        with self._count_lock: self.worker_count+=1
        try: super().process_request(request,address)
        except Exception:
            self._release_worker();raise

    def _release_worker(self):
        with self._count_lock: self.worker_count-=1
        self._capacity.release()

    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally: self._release_worker()

    def handle_error(self,request,address):
        # Input deadline disconnects are expected; arbitrary exception text can
        # contain provider credentials. Keep only a bounded diagnostic counter.
        with self._count_lock:self.request_errors+=1


def _chat_for(service):
    from backend.chat.service import ChatService
    service.chat=ChatService(service)
    return service.chat


def create_server(service, token, port=8000, identities=None, *, max_workers=64, input_timeout=10, chat_service=None, feishu_channel=None,
                  access=None):
    if type(max_workers) is not int or not 1<=max_workers<=256:
        raise Rejected('INVALID_HTTP_CAPACITY')
    if type(input_timeout) not in (int,float) or not .05<=input_timeout<=30:
        raise Rejected('INVALID_HTTP_INPUT_TIMEOUT')
    identity_map = dict(identities) if identities is not None else {token:"demo"}
    if not identity_map or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v for k,v in identity_map.items()):
        raise Rejected("INVALID_LOCAL_IDENTITIES")
    from backend.access import AccessControl
    access = access or getattr(service, "access", None) or AccessControl(service)
    service.access = access
    from backend.tx.api import TransactionQueries
    tx_queries=TransactionQueries(service.db)
    from backend.chat import http as chat_http
    chat=chat_service if chat_service is not None else (getattr(service,'chat',None) or (_chat_for(service) if service.banking else None))
    if feishu_channel is None and chat is not None:
        from backend.channels.feishu import FeishuChannel, FeishuConfig
        feishu_config=FeishuConfig.from_env()
        # Long-connection mode runs in its own worker (scripts.feishu_worker); the HTTP webhook is only for webhook mode.
        feishu_channel=FeishuChannel(chat,feishu_config) if feishu_config.enabled and feishu_config.mode=="webhook" else None
    feishu=feishu_channel
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(input_timeout)
            self._input_timer=threading.Timer(input_timeout,self._expire_input)
            self._input_timer.daemon=True;self._input_timer.start()

        def _expire_input(self):
            try:self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass

        def finish(self):
            self._input_timer.cancel()
            super().finish()

        def log_message(self, format, *args):
            pass  # No authorization headers, user input, or bank facts in HTTP access logs.

        def reply(self, status, body, content_type="application/json; charset=utf-8"):
            data = canonical(body).encode() if content_type.startswith("application/json") else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            security_headers(self)
            self.end_headers()
            self.wfile.write(data)

        def reply_static(self, path, data, content_type):
            """App files: hashed bundles are cached for good, media supports Range (Safari will not play video without it)."""
            status, total, start, end = 200, len(data), 0, len(data) - 1
            spec = self.headers.get("Range", "")
            if spec.startswith("bytes=") and content_type.startswith("video/"):
                first, _, last = spec[6:].split(",")[0].partition("-")
                try:
                    if first:
                        start, end = int(first), min(int(last) if last else total - 1, total - 1)
                    else:
                        start, end = max(0, total - int(last)), total - 1
                except ValueError:
                    start, end = 0, total - 1
                if start > end or start >= total:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.send_header("Content-Length", "0")
                    security_headers(self)
                    self.end_headers()
                    return
                status = 206
            body = data[start:end + 1]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            if content_type.startswith("video/"):
                self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if path.startswith("/assets/")
                             else "public, max-age=86400" if path.startswith(("/bg/", "/hero/", "/figs/", "/fonts/", "/icon", "/apple-touch")) else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            security_headers(self)
            self.end_headers()
            self.wfile.write(body)

        def check_origin(self):
            """Host / Origin allow-list (DNS-rebinding defence). Which hosts are allowed depends on the access mode."""
            if any(len(self.headers.get_all(k,[]))>1 for k in ('Host','Origin','Authorization','X-TRUSTA-Operator')):
                raise Rejected('UNAUTHENTICATED')
            port = self.server.server_port
            if self.headers.get("Host") not in access.allowed_hosts(port):
                raise Rejected("INVALID_HOST")
            origin = self.headers.get("Origin")
            if origin and origin not in access.allowed_origins(port):
                raise Rejected("INVALID_ORIGIN")

        def is_local(self):
            return access.is_local(self.headers.get("Host"), self.client_address[0], self.server.server_port)

        def identity(self):
            self.check_origin()
            actual = self.headers.get("Authorization", "")
            matches=[user for key,user in identity_map.items() if hmac.compare_digest(actual.encode(),f"Bearer {key}".encode())]
            if len(matches)==1:
                # The master token never works from another device: phones pair and get their own revocable token.
                if not self.is_local():
                    raise Rejected("UNAUTHENTICATED")
                self.via_device = False
                return matches[0]  # Server-configured local identity; client cannot override it.
            owner = access.device_owner(actual[7:]) if actual.startswith("Bearer ") else None
            if owner is None:
                raise Rejected("UNAUTHENTICATED")
            self.via_device = True
            return owner

        def do_GET(self):
            self._input_timer.cancel()
            try:
                path = urlparse(self.path).path
                if path == "/health":
                    worker=getattr(service,'checkpoint_worker_status',None)
                    health={"status":"degraded" if worker and worker['status']=='degraded' else "ok",
                            "bank":service.gateway._adapter.provider.manifest.provider}
                    if worker:health['checkpoint_worker']=dict(worker)
                    return self.reply(200,health)
                if path == "/api/access/info":
                    self.check_origin()  # public on purpose: tells a phone which address / mode it is talking to
                    return self.reply(200, {"mode": access.mode, "local_request": self.is_local()})
                static = serve_static(path)
                if static:
                    return self.reply_static(path, *static)
                if path.startswith(("/bg/", "/hero/", "/figs/", "/fonts/", "/assets/")):
                    return self.reply(404, {"error": "NOT_FOUND"})  # optional media (e.g. a custom pool floor) — no auth needed to say "absent"
                user_id = self.identity()
                if path == "/api/access":
                    return self.reply(200, {"mode": access.mode, "urls": access.urls(self.server.server_port),
                                            "devices": access.devices(user_id), "via_device": self.via_device})
                if path.startswith('/api/chat/') and chat is not None:
                    return chat_http.handle_get(self, chat, path, user_id)
                if path.startswith('/api/v1/tx/') and len(path.split('/'))==5:
                    return self.reply(200,tx_queries.transaction(path.split('/')[-1],user_id))
                if path.startswith('/api/v1/anchor/') and len(path.split('/'))==5:
                    return self.reply(200,tx_queries.anchor(path.split('/')[-1],user_id))
                if path == '/api/bank':
                    from backend.bank_sdk.presentation import bank_overview
                    return self.reply(200,bank_overview(service,user_id))
                if path == '/api/security/status':
                    return self.reply(200, service.safety.status(user_id))
                if path in {'/api/identity/did','/api/identity/resolve'}:
                    resolver=service.extensions.require('did')
                    params=parse_qs(urlparse(self.path).query,keep_blank_values=True)
                    key='agent' if path.endswith('/did') else 'did'
                    if set(params)!={key} or len(params[key])!=1: raise Rejected('INVALID_READ_PARAMETERS')
                    return self.reply(200,resolver.export(params[key][0]) if key=='agent' else resolver.resolve(params[key][0]))
                if path=='/api/events' and hasattr(service,'events'):
                    return self.reply(200,{'events':service.events.list(user_id)})
                if path=='/api/reminders' and hasattr(service,'events'):
                    params=parse_qs(urlparse(self.path).query,keep_blank_values=True)
                    if set(params)!={'account_id'} or len(params['account_id'])!=1: raise Rejected('INVALID_READ_PARAMETERS')
                    return self.reply(200,service.events.reminders(user_id,params['account_id'][0]))
                if path == '/api/security/reviews':
                    return self.reply(200, {'cases':service.safety.cases(user_id)})
                if path == '/api/payees':
                    from backend.bank_sdk.presentation import bank_payees
                    return self.reply(200,{'payees':bank_payees(service,user_id)})
                if path == '/api/transactions':
                    params=parse_qs(urlparse(self.path).query,keep_blank_values=True)
                    if set(params)!={'account_id','month'} or any(len(v)!=1 for v in params.values()):
                        raise Rejected('INVALID_READ_PARAMETERS')
                    return self.reply(200,{'records':service.finance.records(user_id,params['account_id'][0],params['month'][0])})
                if path.startswith("/api/splits/") and len(path.split("/"))==4 and hasattr(service,"splits"):
                    return self.reply(200,service.splits.get(path.split("/")[-1],user_id))
                if path == "/api/memory":
                    return self.reply(200, {"entries": service.memory.list(user_id)})
                if path == "/api/platform/security":
                    return self.reply(200, service.security_status())
                if path == "/api/audit/proof":
                    if not service.records:
                        raise Rejected("PHASE4_DISABLED")
                    params = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                    if len(params) != 1 or set(params) - {"audit_event_id", "receipt_id", "transaction_id"} or any(len(v) != 1 for v in params.values()):
                        raise Rejected("INVALID_RECORD_QUERY")
                    with service.lock:
                        bundle = service.records.query(user_id, **{key: values[0] for key, values in params.items()})
                        bundle["anchor_verified"] = service.anchors.verify_anchor(bundle["checkpoint"]["checkpoint_id"])
                        return self.reply(200, bundle)
                if path.startswith("/api/conversations/") and len(path.split("/")) == 4:
                    return self.reply(200, service.conversations.get(path.split("/")[-1], user_id))
                if path.startswith("/api/llm-calls/") and len(path.split("/")) == 4:
                    return self.reply(200, service.llm.trace(path.split("/")[-1], user_id))
                if path == "/api/tasks":
                    with service.lock:
                        tasks = service.blackboard.list(user_id)
                        return self.reply(200, {"tasks": [{"task_id": t["task_id"], "task_type": t["task_type"], "user_goal": t["user_goal"], "status": t["status"]} for t in tasks]})
                if path.startswith("/api/tasks/") and len(path.split("/")) == 4:
                    return self.reply(200, service.get_task(path.split("/")[-1], user_id))
                if path == "/api/balance":
                    params=parse_qs(urlparse(self.path).query,keep_blank_values=True)
                    if set(params)-{'account_id'} or any(len(v)!=1 for v in params.values()): raise Rejected('INVALID_READ_PARAMETERS')
                    account_id=params.get('account_id',['acct-demo'])[0]
                    return self.reply(200, service.gateway.read("verifier", user_id, "account.get_balance", account_id=account_id))
                if path == "/api/audit":
                    with service.lock:
                        return self.reply(200, {"valid": service.audit.verify_chain()})
                self.reply(404, {"error": "NOT_FOUND"})
            except (Rejected, InferenceError) as exc:
                if urlparse(self.path).path.startswith(("/api/chat/","/api/channels/")) and exc.code.startswith(("CHAT_","ACCOUNT_","ACTION_","DEMO_","SMS_","HANDOFF_","SECURITY_OPERATOR","FEISHU_","TRACE_","AUTOMATION_","PROTECTION_","FEATURE_","SETTINGS_","INVALID_INPUT")):
                    return self.reply(chat_http.status_for(exc.code), {"error": exc.code, "message": str(exc)})
                self.reply(403 if exc.code in {"TX_ACCESS_DENIED", "UNAUTHENTICATED", "INVALID_ORIGIN", "INVALID_HOST", "TASK_ACCESS_DENIED", "CONVERSATION_ACCESS_DENIED", "LLM_CALL_ACCESS_DENIED", "MEMORY_ACCESS_DENIED", "AUDIT_ACCESS_DENIED"} else 400,
                           {"error": exc.code, "message": str(exc)})
            except Exception:
                self.reply(500, {"error": "INTERNAL_ERROR", "message": "状态未确认，请刷新任务；不要重复提交转账。"})

        def do_POST(self):
            try:
                if len(self.headers.get_all('Content-Length',[]))!=1 or self.headers.get_all('Transfer-Encoding'):
                    raise Rejected('INVALID_BODY_SIZE')
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise Rejected("INVALID_CONTENT_TYPE")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise Rejected("INVALID_BODY_SIZE")
                from backend.llm.validation import strict_json
                try:
                    raw=self.rfile.read(size)
                    self._input_timer.cancel()  # Business execution is not an input read.
                    body=strict_json(raw)
                except InferenceError: raise Rejected('INVALID_INPUT') from None
                if not isinstance(body, dict):
                    raise Rejected("INVALID_INPUT")
                # IM platform callbacks carry platform signatures, not the local bearer token.
                if urlparse(self.path).path == "/api/channels/feishu/webhook" and feishu is not None:
                    return self.reply(200, feishu.handle(self.headers, raw))
                if urlparse(self.path).path == "/api/pairing/claim":
                    # A phone exchanges a one-time code (from the QR / typed) for its own device token.
                    self.check_origin()
                    if set(body) - {"code", "name"}:
                        raise Rejected("INVALID_INPUT")
                    return self.reply(201, access.pair_device(body.get("code"), body.get("name", "手机")))
                # Consume bounded input before rejecting identity, avoiding TCP resets
                # from unread POST bytes when returning a 403 on Windows.
                user_id = self.identity()
                path = urlparse(self.path).path
                if path == "/api/pairing/start":
                    # Only someone sitting at this computer can mint a pairing code (QR shown on the desktop page).
                    if self.via_device or not self.is_local() or set(body) - {"purpose"}:
                        raise Rejected("PAIRING_LOCAL_ONLY" if set(body) <= {"purpose"} else "INVALID_INPUT")
                    return self.reply(201, access.start_pairing(user_id, body.get("purpose", "device"), self.server.server_port))
                if path.startswith("/api/pairing/devices/") and path.endswith("/revoke") and not body:
                    return self.reply(200, access.revoke(user_id, path.split("/")[4]))
                if path == "/api/channels/feishu/unbind" and not body:
                    return self.reply(200, access.unbind_channel("feishu", user_id))
                if path.startswith("/api/chat/") and chat is not None:
                    return chat_http.handle_post(self, chat, path, body, user_id)
                if path == "/api/tasks":
                    if set(body) - {"text", "source_account_id"} or not isinstance(body.get("text"), str):
                        raise Rejected("INVALID_INPUT")
                    return self.reply(201, service.create_task(body["text"], user_id, body.get("source_account_id", "acct-demo")))
                parts = path.strip("/").split("/")
                if parts==['api','privacy','prove'] and set(body)=={'task_id','action_hash'}:
                    return self.reply(201,service.extensions.require('zkp').prove(body['task_id'],user_id,body['action_hash']))
                if parts==['api','privacy','verify'] and set(body)=={'bundle'}:
                    return self.reply(200,service.extensions.require('zkp').verify(user_id,body['bundle']))
                if parts==['api','events'] and hasattr(service,'events'):
                    if set(body)!={'request_id','account_id','event_type','execute_at','parameters'}: raise Rejected('INVALID_INPUT')
                    return self.reply(201,service.events.create(user_id,**body))
                if parts==['api','events','tick'] and hasattr(service,'events') and not body:
                    return self.reply(200,service.events.tick(user_id))
                if len(parts)==4 and parts[:2]==['api','events'] and parts[3]=='cancel' and hasattr(service,'events') and not body:
                    return self.reply(200,service.events.cancel(parts[2],user_id))
                if parts == ['api','security','totp','enroll'] and not body:
                    return self.reply(201,service.safety.begin_enrollment(user_id))
                if parts == ['api','security','totp','confirm'] and set(body)=={'code'}:
                    return self.reply(200,service.safety.confirm_enrollment(user_id,body['code']))
                if parts == ['api','security','webauthn','register'] and not body:
                    if not service.safety.enabled: raise Rejected('COMPETITION_SAFETY_DISABLED')
                    return self.reply(200,service.safety.webauthn.begin_registration(user_id,self.headers.get('Origin','')))
                if parts == ['api','security','webauthn','finish']:
                    return self.reply(200,service.safety.webauthn.finish_registration(user_id,body))
                if parts == ['api','security','webauthn','remove'] and set(body)=={'id'}:
                    return self.reply(200,service.safety.webauthn.remove(user_id,body['id']))
                if parts == ['api','security','unlock'] and set(body)=={'reason'}:
                    return self.reply(200,service.safety.unlock(user_id,self.headers.get('X-TRUSTA-Operator',''),body['reason']))
                if parts==['api','graphs'] and hasattr(service,'graphs'):
                    if set(body)!={'goal','graph','inputs'}: raise Rejected('INVALID_INPUT')
                    return self.reply(201,service.graphs.create(user_id,body['goal'],body['graph'],body['inputs']))
                if parts==['api','graphs','propose'] and hasattr(service,'graphs'):
                    if set(body)!={'goal','inputs'}: raise Rejected('INVALID_INPUT')
                    return self.reply(201,service.graphs.propose(user_id,body['goal'],body['inputs']))
                if parts==['api','graphs','dialogue'] and hasattr(service,'graphs'):
                    if set(body)!={'goal','inputs'}: raise Rejected('INVALID_INPUT')
                    from backend.llm.proposal_dialogue import ProposalDialogue
                    return self.reply(201,ProposalDialogue(service).start(user_id,body['goal'],body['inputs']))
                if parts==['api','graphs','from-text'] and hasattr(service,'graphs'):
                    if set(body)!={'text','account_id'}: raise Rejected('INVALID_INPUT')
                    from backend.llm.grounded_request import GroundedRequestPlanner
                    return self.reply(201,GroundedRequestPlanner(service).propose(user_id,body['text'],body['account_id']))
                if parts==['api','workflows','subscription-budget'] and hasattr(service,'graphs'):
                    if set(body)!={'text','account_id'}: raise Rejected('INVALID_INPUT')
                    from backend.llm.subscription_workflow import SubscriptionBudgetDialogue
                    return self.reply(201,SubscriptionBudgetDialogue(service).start(user_id,body['text'],body['account_id']))
                if len(parts)==4 and parts[:2]==['api','workflows'] and parts[3]=='select' and hasattr(service,'graphs'):
                    if set(body)!={'revision','text'}: raise Rejected('INVALID_INPUT')
                    from backend.llm.subscription_workflow import SubscriptionBudgetDialogue
                    return self.reply(201,SubscriptionBudgetDialogue(service).select(user_id,parts[2],body['revision'],body['text']))
                if len(parts)==4 and parts[:2]==['api','graphs'] and hasattr(service,'graphs'):
                    task_id,op=parts[2:]
                    if op=='continue' and set(body)=={'revision','patch'} and type(body['revision']) is int:
                        from backend.llm.proposal_dialogue import ProposalDialogue
                        return self.reply(201,ProposalDialogue(service).resume(user_id,task_id,body['revision'],body['patch']))
                    if op=='cancel' and set(body)=={'revision'} and type(body['revision']) is int:
                        if not service.graphs.cancel(task_id,user_id,body['revision']): raise Rejected('GRAPH_CANCEL_DENIED')
                        return self.reply(200,service.get_task(task_id,user_id))
                    if op=='resume' and not body: return self.reply(200,service.graphs.advance(task_id,user_id))
                    if op=='signal' and set(body)=={'node_id','value'}: return self.reply(200,service.graphs.signal(task_id,user_id,body['node_id'],body['value']))
                    if op=='authorize' and set(body)-{'node_id','action_hash','approve','strong_secret'}==set():
                        return self.reply(200,service.graphs.authorize(task_id,user_id,body['node_id'],body['action_hash'],body['approve'],body.get('strong_secret','')))
                    raise Rejected('INVALID_INPUT')
                if parts==["api","splits"] and hasattr(service,"splits"):
                    if set(body)-{"account_id","total_amount","participants","method","values","expires_at"}:
                        raise Rejected("INVALID_INPUT")
                    return self.reply(201,service.splits.create(user_id,body["account_id"],body["total_amount"],body["participants"],body.get("method","EQUAL"),body.get("values"),body.get("expires_at")))
                if len(parts)==4 and parts[:2]==["api","splits"] and hasattr(service,"splits"):
                    bill_id,op=parts[2:]
                    if op in {"publish","cancel","decline"}:
                        if body: raise Rejected("INVALID_INPUT")
                        return self.reply(200,getattr(service.splits,op)(bill_id,user_id))
                    if op=="prepare":
                        if set(body)!={"source_account_id"}: raise Rejected("INVALID_INPUT")
                        return self.reply(200,service.splits.prepare_payment(bill_id,user_id,body["source_account_id"]))
                    if op=="pay":
                        if set(body)-{"action_hash","approve","strong_secret"} or type(body.get("approve")) is not bool: raise Rejected("INVALID_INPUT")
                        return self.reply(200,service.splits.pay(bill_id,user_id,body["action_hash"],body["approve"],body.get("strong_secret","")))
                if len(parts)==6 and parts[:2]==["api","tasks"] and parts[3]=="actions" and service.banking:
                    task_id,action_id,operation=parts[2],parts[4],parts[5]
                    if operation=="authorize":
                        if (set(body)-{"action_hash","approve","strong_secret"} or type(body.get("approve")) is not bool
                                or not isinstance(body.get("action_hash"),str) or not isinstance(body.get("strong_secret",""),str)):
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200,service.banking.authorize(task_id,user_id,body["action_hash"],body["approve"],body.get("strong_secret",""),action_id))
                    if operation=="resume":
                        if body:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200,service.banking.refresh(task_id,user_id,action_id))
                if len(parts) == 3 and parts[:2] == ["api", "memory"]:
                    operation = parts[2]
                    if operation == "preferences":
                        if set(body) - {"key", "value", "consent", "ttl_seconds"}:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(201, service.memory.preference(user_id, body.get("key"), body.get("value"),
                                          body.get("consent"), body.get("ttl_seconds", service.memory.DEFAULT_TTL)))
                    if operation == "conversation-reference":
                        if set(body) - {"conversation_id", "consent", "ttl_seconds"}:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(201, service.memory.conversation_reference(user_id, body.get("conversation_id"),
                                          body.get("consent"), body.get("ttl_seconds", service.memory.DEFAULT_TTL)))
                    if operation == "risk-reference":
                        if set(body) - {"consent", "ttl_seconds"}:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(201, service.remember_risk_reference(user_id, body.get("consent"),
                                          body.get("ttl_seconds", service.memory.DEFAULT_TTL)))
                    if operation == "clear":
                        if body:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200, service.memory.clear(user_id))
                if len(parts) == 4 and parts[:2] == ["api", "memory"] and parts[3] == "forget":
                    if body:
                        raise Rejected("INVALID_INPUT")
                    return self.reply(200, service.memory.forget(user_id, parts[2]))
                if parts == ["api", "conversations"]:
                    if body:
                        raise Rejected("INVALID_INPUT")
                    return self.reply(201, service.conversations.create(user_id))
                if len(parts) == 4 and parts[:2] == ["api", "conversations"]:
                    conversation_id, operation = parts[2:]
                    if operation == "turns":
                        if set(body) - {"turn_id", "revision", "text", "source_account_id"}:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200, service.conversations.send(conversation_id, user_id, body.get("turn_id"),
                                         body.get("revision"), body.get("text"), body.get("source_account_id", "acct-demo")))
                    if operation == "close":
                        if body:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200, service.conversations.close(conversation_id, user_id))
                if len(parts) == 4 and parts[:2] == ["api", "tasks"]:
                    task_id, operation = parts[2:]
                    if operation == 'review' and set(body)=={'note'}:
                        return self.reply(201,service.safety.review(service._owned_task(task_id,user_id),user_id,body['note']))
                    if operation == 'review-note' and set(body)=={'note'}:
                        service._owned_task(task_id,user_id)
                        return self.reply(200,service.safety.annotate_review(task_id,user_id,self.headers.get('X-TRUSTA-Operator',''),body['note']))
                    if operation=='reconcile':
                        if body:raise Rejected('INVALID_INPUT')
                        from backend.bank_sdk.reconciliation import reconcile
                        return self.reply(200,reconcile(service,task_id,user_id))
                    if operation == "authorize":
                        if (set(body) - {"action_hash", "approve", "strong_secret"} or type(body.get("approve")) is not bool
                                or not isinstance(body.get("action_hash"), str) or not isinstance(body.get("strong_secret", ""), str)):
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200, service.authorize(task_id, user_id, body["action_hash"], body["approve"], body.get("strong_secret", "")))
                    if operation == "renew":
                        if body:
                            raise Rejected("INVALID_INPUT")
                        return self.reply(200, service.renew_authorization(task_id, user_id))
                self.reply(404, {"error": "NOT_FOUND"})
            except (ValueError, TypeError, KeyError):
                self.reply(400, {"error": "INVALID_INPUT"})
            except (Rejected, InferenceError) as exc:
                if urlparse(self.path).path.startswith(("/api/chat/","/api/channels/")) and exc.code.startswith(("CHAT_","ACCOUNT_","ACTION_","DEMO_","SMS_","HANDOFF_","SECURITY_OPERATOR","FEISHU_","TRACE_","AUTOMATION_","PROTECTION_","FEATURE_","SETTINGS_","INVALID_INPUT")):
                    return self.reply(chat_http.status_for(exc.code), {"error": exc.code, "message": str(exc)})
                self.reply(429 if exc.code=="PAIRING_RATE_LIMITED" else 403 if exc.code in {"UNAUTHENTICATED", "INVALID_ORIGIN", "INVALID_HOST", "TASK_ACCESS_DENIED", "CONVERSATION_ACCESS_DENIED", "LLM_CALL_ACCESS_DENIED", "MEMORY_ACCESS_DENIED", "PAIRING_LOCAL_ONLY", "PAIRING_INVALID"} else 400,
                           {"error": exc.code, "message": str(exc)})
            except Exception:
                self.reply(500, {"error": "INTERNAL_ERROR", "message": "状态未确认，请刷新任务；不要重复提交转账。"})

    server = BoundedHTTPServer((access.bind_host(), port), Handler,max_workers)
    server.daemon_threads = True
    return server


def main():
    config = Config.local()
    service = TrustaService(config)
    token_file = Path(config.db_path).parent / "api.token"
    if config.api_token:
        token = config.api_token
    else:
        from backend.security.local_files import write_secret_atomic, protect_secret
        if not token_file.exists():
            write_secret_atomic(token_file, secrets.token_urlsafe(32).encode(), create_only=True)
        protect_secret(token_file)
        token = token_file.read_text(encoding="utf-8").strip()
    server = create_server(service, token)
    print("TRUSTA V1 — 本地虚拟银行")
    print("Trusted Communication: V3 / ECDSA-P256-SHA256" if config.communication_version == 3 else "Trusted Communication: V2 / HMAC-SHA256 (explicit compatibility profile)")
    chat = getattr(service, "chat", None)
    if chat is not None:
        mode = "DeepSeek（" + chat.llm.model + "）" if getattr(chat.llm, "available", False) else "规则兜底（未配置 DEEPSEEK_API_KEY）"
        print("对话理解：" + mode)
    print(f"打开 http://localhost:8000/#token={token}")
    access = service.access
    if access.warning:
        print("注意：" + access.warning)
    if access.mode == "lan":
        print(f"局域网模式：手机连同一 Wi-Fi / 热点后访问 {access.urls(8000)['lan']}")
        print("  手机首次使用需配对：在电脑页面点“连接手机”扫码，或另开窗口运行 python -m scripts.pair")
    elif access.mode == "tunnel":
        print(f"隧道模式：公网地址 https://{access.public_host}/ （由隧道工具转发到本机 8000 端口）")
        print("  手机首次使用需配对：python -m scripts.pair")
    else:
        print("仅本机访问（如需手机访问：python -m scripts.setup 把“访问方式”改为局域网）")
    print("Ctrl+C 停止；任务和银行状态将保留。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

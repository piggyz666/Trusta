"""Six-agent orchestration and independent trusted action branches."""
import json
import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from backend.domain import Rejected, canonical, digest, uid
from backend.runtime.banking_plan import OWNERS, TASKS

TERMINAL = {"COMPLETED", "DENIED", "BLOCKED", "FAILED", "MANUAL_REVIEW", "SUPERSEDED"}


def task_claim(method):
    @wraps(method)
    def guarded(self,task_id,*args,**kwargs):
        with self.claims.hold(task_id,reentrant=True):
            return method(self,task_id,*args,**kwargs)
    return guarded


class BankingRuntime:
    def __init__(self, service):
        self.s = service
        from backend.runtime.graph_claims import GraphClaims
        self.claims=GraphClaims(service)

    def _save(self, task_id, records, **changes):
        return self.s.blackboard.update(task_id,"runtime",action_records=records,
                    proposed_actions=[row["action"] for row in records],
                    authorizations=[row["authorization"] for row in records if row.get("authorization")],
                    verifications=[row["verification"] for row in records if row.get("verification")],
                    receipts=[row["receipt"] for row in records if row.get("receipt")],**changes)

    def _work(self, task_id, step_id, actor, fn):
        result = self.s._step(task_id,step_id,fn)
        self.s._agent_message(actor,"supervisor",task_id,step_id,"READ_ONLY_REPORT",result)
        self.s.audit.append(task_id,None,"BANKING_READ_RESULT",actor,{"step_id":step_id,"result_hash":digest(result)})
        return result

    def create(self, text, user_id, account_id, normalized):
        s = self.s
        account_id = normalized.get("source_account_id",account_id)
        with s.lock:
            task_id = s.supervisor.receive(user_id,text,normalized)
        with self.claims.hold(task_id,reentrant=True):
            return self._create_claimed(task_id,text,user_id,account_id,normalized)

    def _create_claimed(self,task_id,text,user_id,account_id,normalized):
        s=self.s
        with s.lock:
            s.blackboard.update(task_id,"runtime",status="RUNNING",action_records=[],candidates=[],scheduled_job_refs=[])
            s._event("TASK_CREATED",task_id,"supervisor",normalized)
        try:
            s._agent_message("supervisor","planner",task_id,"R01","REQUEST",normalized)
            plan = s.planner.plan(s.blackboard.get(task_id))
            s._agent_message("planner",OWNERS[normalized["task_type"]],task_id,"R01","PLAN",plan)
            s.blackboard.install_plan(task_id,plan)
            s.scheduler.start(task_id,"R01")
            if normalized.get("needs_clarification"):
                s.scheduler.transition(task_id,"R01","WAITING_USER")
                return s.blackboard.update(task_id,"runtime",status="WAITING_USER",clarification=normalized["clarification"])
            s.scheduler.transition(task_id,"R01","COMPLETED")
            kind = normalized["task_type"]
            actions = []
            if kind == "CROSS_SCENARIO":
                # Actual independent read branches are concurrently executed. Persistence is serialized afterwards.
                for step in ("R02","R03"):
                    s.scheduler.start(task_id,step)
                def analyze():
                    account = s.finance.account(user_id,account_id)
                    current = s.finance.records(user_id,account_id,normalized["month"])
                    return {"account":account,"summary":s.finance.summarize(current)}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    finance = pool.submit(analyze)
                    authority = pool.submit(s.service_agent.resources,user_id,"subscription")
                    analysis,subscriptions = finance.result(),authority.result()
                for step,actor,result in (("R02","finance",analysis),("R03","service",subscriptions)):
                    s.scheduler.transition(task_id,step,"COMPLETED")
                    s._agent_message(actor,"supervisor",task_id,step,"READ_ONLY_REPORT",result)
                candidates = self._work(task_id,"R04","finance",lambda:s.finance.recurring(user_id,account_id))
                resolved = self._work(task_id,"R05","service",lambda:s.service_agent.resolve_subscriptions(candidates,subscriptions))
                s.blackboard.update(task_id,"runtime",candidates=resolved,result={"analysis":analysis,"resolutions":resolved,
                                         "decision":"User selects each subscription; unnecessary spending is not inferred as consent"})
                task = s.blackboard.get(task_id)
                for row in resolved:
                    if row["resolution"] == "RESOLVED":
                        actions.append(s.service_agent.propose(task,"SUBSCRIPTION_CANCEL",row["subscription_id"],{},account_id))
            else:
                owner = OWNERS[kind]
                account = self._work(task_id,"R02",owner,lambda: s.gateway.read(owner,user_id,"account.get",account_id=account_id)) if kind!="PRODUCT_COMPARE" else self._work(task_id,"R02",owner,lambda:s.wealth.products(user_id))
                def propose():
                    task = s.blackboard.get(task_id)
                    if kind == "PRODUCT_COMPARE":
                        return {"report":s.wealth.compare(user_id,normalized["product_ids"]),"actions":[]}
                    if kind == "SPLIT_REQUEST":
                        payees = [s.transaction.recipient(user_id,name) for name in normalized["participants"]]
                        if len({payee["id"] for payee in payees}) != len(payees):
                            raise Rejected("DUPLICATE_SPLIT_RECIPIENT")
                        base,remainder = divmod(normalized["amount"],len(payees))
                        return {"actions":[],"report":{"split_requests":[{"payee_id":payee["id"],"amount":base+int(i<remainder),
                            "currency":"CNY","status":"REQUEST_ONLY"} for i,payee in enumerate(payees)],"total":normalized["amount"],"financial_write":False}}
                    if kind == "SCHEDULED_TRANSFER":
                        recipient = s.transaction.recipient(user_id,normalized["recipient_name"])
                        balance = s.transaction.balance(user_id,account_id)
                        return {"actions":[s.transaction.propose(task,recipient,account,balance)]}
                    if normalized.get("operation") in {"LIST","DETECT"}:
                        resource = {"CARD_MANAGEMENT":"card","SUBSCRIPTION_MANAGEMENT":"subscription","DIRECT_DEBIT_MANAGEMENT":"direct_debit"}[kind]
                        report={"resources":s.service_agent.resources(user_id,resource)}
                        if normalized["operation"]=="DETECT":
                            report["candidates"]=s.finance.recurring(user_id,account_id)
                            report["resolutions"]=s.service_agent.resolve_subscriptions(report["candidates"],report["resources"])
                        return {"report":report,"actions":[]}
                    if kind in {"INVESTMENT_PURCHASE","INVESTMENT_REDEEM"}:
                        action=s.wealth.propose(task,kind,normalized["target_id"],normalized["parameters"],account_id,s.config.action_ttl)
                    else:
                        action=s.service_agent.propose(task,normalized["operation"],normalized["target_id"],normalized["parameters"],account_id)
                    return {"actions":[action]}
                proposed = self._work(task_id,"R03",owner,propose)
                actions = proposed["actions"]
                if "report" in proposed:
                    s.blackboard.update(task_id,"runtime",result=proposed["report"])
            for action in actions:
                self._register(task_id,action)
            if kind == "SCHEDULED_TRANSFER" and actions:
                entry = s.blackboard.get(task_id)["action_records"][0]
                job={"schedule_id":uid(),"task_id":task_id,"action_template":normalized,"execute_at":normalized["execute_at"],
                     "timezone":"Asia/Shanghai","status":"PENDING_AUTH","created_by":user_id,"source_account_id":account_id,
                     "authorization_binding":None,"policy_snapshot":entry["policy"],"idempotency_key":actions[0]["idempotency_key"],
                     "action_id":actions[0]["action_id"],"created_at":time.time()}
                with s.db.transaction() as conn:
                    conn.execute("INSERT INTO scheduled_jobs VALUES(?,?,?,?,?)",(job["schedule_id"],task_id,job["execute_at"],job["status"],self._encode_job(job)))
                s.blackboard.update(task_id,"runtime",scheduled_job_refs=[job["schedule_id"]])
                s.audit.append(task_id,actions[0]["action_id"],"SCHEDULE_CREATED","runtime",job)
            return self.aggregate(task_id)
        except Rejected as exc:
            return self._fail(task_id,exc.code,"WAITING_USER" if exc.code=="RECIPIENT_AMBIGUOUS" else "BLOCKED")
        except Exception:
            return self._fail(task_id,"BANKING_READ_UNCERTAIN","FAILED")

    def _fail(self, task_id, code, status):
        task=self.s.blackboard.get(task_id)
        for step in task["steps"]:
            if step["status"]=="RUNNING":
                self.s.scheduler.transition(task_id,step["step_id"],"WAITING_USER" if status=="WAITING_USER" else status)
        self.s.audit.append(task_id,None,"BANKING_TASK_REJECTED","runtime",{"reason":code})
        return self.s.blackboard.update(task_id,"runtime",status=status,errors=task["errors"]+[code])

    def _register(self, task_id, action):
        s=self.s
        task=s.blackboard.get(task_id)
        prefix=f"A{len(task['action_records'])+1:02}"
        s.blackboard.extend_action_plan(task_id,prefix,s.planner.action_branch(task,prefix))
        spec_actor=action["initiating_agent"]
        message_step="R05" if task["task_type"]=="CROSS_SCENARIO" else "R03"
        s._agent_message(spec_actor,"supervisor",task_id,message_step,"ACTION_PROPOSED",action)
        with s.db.transaction() as conn:
            conn.execute("INSERT INTO actions VALUES(?,?)",(action["action_id"],canonical(action)))
        def schema():
            s.schema.validate(action)
            s.capabilities.check(spec_actor,"transfer.propose" if action["action_type"]=="TRANSFER" else "wealth.propose" if spec_actor=="wealth" else "service.propose")
            s.capabilities.check("executor",action["requested_capability"])
        s._step(task_id,prefix+"S",schema)
        policy=s._step(task_id,prefix+"P",lambda:s.policy.evaluate(action))
        record={"action":action,"prefix":prefix,"policy":policy,"authorization":None,"execution":None,"verification":None,"receipt":None,
                "status":"BLOCKED" if policy["decision"]=="BLOCK" else "WAITING_USER"}
        if policy["decision"]=="BLOCK":
            s.scheduler.transition(task_id,prefix+"A","BLOCKED")
        else:
            record["authorization"]=s.auth.request(action,policy)
            s.scheduler.start(task_id,prefix+"A")
            s.scheduler.transition(task_id,prefix+"A","WAITING_USER")
        records=s.blackboard.get(task_id)["action_records"]+[record]
        self._save(task_id,records)
        s._event("AUTHORIZATION_REQUIRED" if record["authorization"] else "POLICY_BLOCKED",task_id,"authorization",{"action_id":action["action_id"],"action_hash":digest(action)})
        return record

    def aggregate(self, task_id):
        task=self.s.blackboard.get(task_id)
        records=task.get("action_records",[])
        statuses={row["status"] for row in records if row["status"]!="SUPERSEDED"}
        status = ("MANUAL_REVIEW" if "MANUAL_REVIEW" in statuses else "WAITING_USER" if "WAITING_USER" in statuses else
                  "WAITING_TIME" if "SCHEDULED" in statuses else "FAILED" if "FAILED" in statuses else
                  "BLOCKED" if "BLOCKED" in statuses else "CANCELLED" if statuses=={"DENIED"} else "COMPLETED")
        return self.s.blackboard.update(task_id,"runtime",status=status)

    def _cancel_branch(self, task_id, prefix):
        for step in self.s.blackboard.get(task_id)["steps"]:
            if step["step_id"].startswith(prefix) and step["status"] in {"PENDING","READY","RUNNING","WAITING_USER","WAITING_TIME"}:
                self.s.scheduler.transition(task_id,step["step_id"],"CANCELLED")

    def jobs(self, task_id):
        return [self._decode_job(row) for row in self.s.db.rows("SELECT * FROM scheduled_jobs WHERE task_id=?",(task_id,))]

    def _encode_job(self, job):
        key=hmac.new(self.s.config.signing_key,b"trusta-scheduled-job-seal-v1",hashlib.sha256).digest()
        seal=hmac.new(key,canonical(job).encode(),hashlib.sha256).hexdigest()
        return canonical(job | {"schedule_seal":seal})

    def _decode_job(self, row):
        try:
            job=json.loads(row["body"])
            seal=job.pop("schedule_seal")
            expected=json.loads(self._encode_job(job))["schedule_seal"]
            if not isinstance(seal,str) or not hmac.compare_digest(seal,expected):
                raise ValueError()
            if any(job[key]!=row[key] for key in ("schedule_id","task_id","execute_at","status")):
                raise ValueError()
            return job
        except (KeyError,TypeError,ValueError):
            raise Rejected("SCHEDULE_TAMPERED") from None

    def save_job(self, job):
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE scheduled_jobs SET status=?,body=? WHERE schedule_id=?",(job["status"],self._encode_job(job),job["schedule_id"]))

    @task_claim
    def authorize(self, task_id, user_id, action_hash, approve, strong_secret="", action_id=None):
        s=self.s
        if type(approve) is not bool:
            raise Rejected("INVALID_INPUT")
        with s.lock:
            task=s._owned_task(task_id,user_id)
            if task["status"]=="MANUAL_REVIEW":
                raise Rejected("MANUAL_REVIEW_REQUIRED")
            records=task.get("action_records",[])
            matches=[row for row in records if digest(row["action"])==action_hash and (action_id is None or row["action"]["action_id"]==action_id)]
            if len(matches)!=1:
                raise Rejected("AUTHORIZATION_HASH_MISMATCH")
            entry=matches[0]
            if entry["status"]!="WAITING_USER" or not entry["authorization"]:
                raise Rejected("ACTION_NOT_AWAITING_AUTHORIZATION")
            job=next((job for job in self.jobs(task_id) if job["action_id"]==entry["action"]["action_id"]),None)
            if job and job["status"] in {"CHECKING","EXECUTING","MANUAL_REVIEW"}:
                raise Rejected("SCHEDULE_BUSY_OR_UNCERTAIN")
            if approve and s.safety.enabled:
                fresh=s.policy.evaluate(entry["action"])
                if fresh["decision"]=="BLOCK":
                    raise Rejected("POLICY_BLOCKED")
                if fresh["decision"]=="STRONG_AUTH" and entry["authorization"]["auth_method"]!="totp":
                    entry["policy"]=fresh
                    entry["authorization"]=s.auth.request(entry["action"],fresh)
                    self._save(task_id,records,clarification="日累计或风险已提高，请核对动作并进行一次性验证码授权。")
                    return self.aggregate(task_id)
            auth=s.auth.decide(entry["authorization"]["authorization_id"],user_id,entry["action"],approve,strong_secret)
            entry["authorization"]=auth
            if auth["status"]=="EXPIRED":
                self._save(task_id,records,clarification="原授权已过期，请恢复任务后核对新动作并重新批准。")
                return self.aggregate(task_id)
            if auth["status"]=="DENIED":
                entry["status"]="DENIED"
                self._cancel_branch(task_id,entry["prefix"])
                if job:
                    job["status"]="CANCELLED"
                    self.save_job(job)
            else:
                s.scheduler.start(task_id,entry["prefix"]+"A")
                s.scheduler.transition(task_id,entry["prefix"]+"A","COMPLETED")
                s.audit.append(task_id,entry["action"]["action_id"],"AUTHORIZATION_GRANTED","authorization",auth)
                if job and time.time()<job["execute_at"]:
                    entry["status"]="SCHEDULED"
                    s.scheduler.transition(task_id,entry["prefix"]+"E","READY")
                    s.scheduler.transition(task_id,entry["prefix"]+"E","WAITING_TIME")
                    job.update(status="SCHEDULED",authorization_binding={"authorization_id":auth["authorization_id"],"action_hash":action_hash},policy_snapshot=entry["policy"])
                    self.save_job(job)
                else:
                    self._save(task_id,records)
                    return self.execute(task_id,entry["action"]["action_id"],job)
            self._save(task_id,records)
            return self.aggregate(task_id)

    @task_claim
    def execute(self, task_id, action_id, job=None):
        s=self.s
        records=s.blackboard.get(task_id)["action_records"]
        entry=next(row for row in records if row["action"]["action_id"]==action_id)
        action,prefix=entry["action"],entry["prefix"]
        try:
            s.schema.validate(action)
            s.capabilities.check(action["initiating_agent"],"transfer.propose" if action["action_type"]=="TRANSFER" else "wealth.propose" if action["initiating_agent"]=="wealth" else "service.propose")
            policy=s.policy.evaluate(action)  # Every execution, including TIME triggers, uses fresh authority.
            if policy["decision"]=="BLOCK":
                raise Rejected("POLICY_BLOCKED")
            auth=s.auth.validate(entry["authorization"]["authorization_id"],action)
            if policy["decision"]=="STRONG_AUTH" and not s.auth.strong_valid(auth):
                raise Rejected("STRONG_AUTH_REQUIRED")
            entry["policy"]=policy
            contract=s.contracts.issue(action,policy["policy_decision_id"],auth["authorization_id"])
        except Rejected as exc:
            entry.update(status="BLOCKED",error=exc.code)
            self._save(task_id,records)
            if job:
                job.update(status="REAUTH_REQUIRED",error=exc.code)
                self.save_job(job)
            return self.aggregate(task_id)
        if job:
            job["status"]="EXECUTING"
            self.save_job(job)
        s.scheduler.start(task_id,prefix+"E")
        entry.update(status="EXECUTING",execution={"contract":contract})
        self._save(task_id,records,status="EXECUTING")
        s.audit.append(task_id,action_id,"EXECUTION_STARTED","executor",{"action_hash":digest(action)})
        try:
            transaction=s.gateway.execute("executor",action,contract)
        except Rejected as exc:
            entry.update(status="FAILED",error=exc.code)
            s.scheduler.transition(task_id,prefix+"E","FAILED")
        except Exception:
            entry.update(status="MANUAL_REVIEW",error="EXECUTION_RESULT_UNCERTAIN")
            s.scheduler.transition(task_id,prefix+"E","MANUAL_REVIEW")
        else:
            entry["execution"]={"contract":contract,"transaction":transaction}
            self._save(task_id,records,status="VERIFYING")
            s.scheduler.transition(task_id,prefix+"E","COMPLETED")
            s.scheduler.start(task_id,prefix+"V")
            try:
                verification=s.verifier.verify(action,transaction["id"])
                s.safety.verification_result(action['customer_id'],verification)
            except Exception:
                entry.update(status='MANUAL_REVIEW',error='VERIFICATION_RESULT_UNCERTAIN')
                s.scheduler.transition(task_id,prefix+'V','MANUAL_REVIEW')
                self._save(task_id,records,status='MANUAL_REVIEW')
                s.audit.append(task_id,action_id,'BANKING_ACTION_RESULT','runtime',{'status':'MANUAL_REVIEW','error':entry['error']})
                if job:
                    job.update(status='MANUAL_REVIEW',error=entry['error']);self.save_job(job)
                return self.aggregate(task_id)
            entry["verification"]=verification
            if verification["status"]!="VERIFIED":
                entry.update(status="MANUAL_REVIEW",error="AUTHORITATIVE_STATE_UNCERTAIN")
                s.scheduler.transition(task_id,prefix+"V","MANUAL_REVIEW")
            else:
                s.scheduler.transition(task_id,prefix+"V","COMPLETED")
                try:
                    entry["receipt"]=s._step(task_id,prefix+"R",lambda:s.receipts.create(action,contract,transaction,verification))
                    entry["status"]="COMPLETED"
                except Exception:
                    entry.update(status="MANUAL_REVIEW",error="RECEIPT_PERSISTENCE_UNCERTAIN")
        self._save(task_id,records,audit_refs=[row["receipt"]["audit_ref"] for row in records if row.get("receipt")])
        s.audit.append(task_id,action_id,"BANKING_ACTION_RESULT","runtime",{"status":entry["status"],"error":entry.get("error")})
        if job:
            job.update(status=entry["status"],error=entry.get("error"))
            self.save_job(job)
        return self.aggregate(task_id)

    @task_claim
    def refresh(self, task_id, user_id, action_id):
        s=self.s
        with s.lock:
            task=s._owned_task(task_id,user_id)
            records=task["action_records"]
            entry=next((row for row in records if row["action"]["action_id"]==action_id),None)
            if task["status"]=="MANUAL_REVIEW" or not entry or entry["status"] not in {"WAITING_USER","SCHEDULED","BLOCKED"}:
                raise Rejected("ACTION_CANNOT_RESUME")
            old=entry["action"]
            if old["action_type"]=="TRANSFER":
                recipient=s.transaction.recipient(user_id,task["context"]["normalized"]["recipient_name"])
                account=s.transaction.account(user_id,old["source_account_id"])
                action=s.transaction.propose(task,recipient,account,s.transaction.balance(user_id,account["id"]))
            elif old["initiating_agent"]=="wealth":
                action=s.wealth.propose(task,old["action_type"],old["target"]["id"],old["parameters"],old["source_account_id"],s.config.action_ttl)
            else:
                action=s.service_agent.propose(task,old["action_type"],old["target"]["id"],old["parameters"],old["source_account_id"])
            action["idempotency_key"]=old["idempotency_key"]  # Resume only before uncertain/known execution; never a new payout key.
            entry["status"]="SUPERSEDED"
            self._cancel_branch(task_id,entry["prefix"])
            self._save(task_id,records)
            record=self._register(task_id,action)
            for job in self.jobs(task_id):
                if job["action_id"]==action_id:
                    job.update(action_id=action["action_id"],authorization_binding=None,policy_snapshot=record["policy"],status="REAUTH_REQUIRED")
                    self.save_job(job)
            return self.aggregate(task_id)

    def tick(self):
        s=self.s
        due=s.db.rows("SELECT schedule_id,task_id FROM scheduled_jobs WHERE execute_at<=? AND status IN ('SCHEDULED','PENDING_AUTH') ORDER BY execute_at,schedule_id",(time.time(),))
        results=[]
        for item in due:
            from contextlib import ExitStack
            with ExitStack() as ownership:
                try: ownership.enter_context(self.claims.hold(item['task_id'],reentrant=True))
                except Rejected as exc:
                    if exc.code=='GRAPH_WORKER_BUSY':continue
                    raise
                with s.db.transaction() as conn:
                    row=conn.execute("SELECT * FROM scheduled_jobs WHERE schedule_id=? AND execute_at<=? AND status IN ('SCHEDULED','PENDING_AUTH')",(item["schedule_id"],time.time())).fetchone()
                    if not row:
                        continue
                    try:
                        job=self._decode_job(row)
                    except Rejected:
                        conn.execute("UPDATE scheduled_jobs SET status='MANUAL_REVIEW' WHERE schedule_id=?",(row["schedule_id"],))
                        s.audit.append_in(conn,row["task_id"],None,"SCHEDULE_TAMPERED","runtime",{"schedule_id":row["schedule_id"]})
                        job=None
                    if job:
                        job["status"]="CHECKING"
                        conn.execute("UPDATE scheduled_jobs SET status='CHECKING',body=? WHERE schedule_id=?",(self._encode_job(job),job["schedule_id"]))
                if job is None:
                    s.blackboard.update(row["task_id"],"runtime",status="MANUAL_REVIEW",errors=["SCHEDULE_TAMPERED"])
                    results.append("MANUAL_REVIEW")
                    continue
                with s.lock:
                    task=s.blackboard.get(job["task_id"])
                    entry=next(row for row in task["action_records"] if row["action"]["action_id"]==job["action_id"])
                    try:
                        s.schema.validate(entry["action"])
                        policy=s.policy.evaluate(entry["action"])
                        if policy["decision"]=="BLOCK":
                            raise Rejected("POLICY_CHANGED")
                        auth=s.auth.validate(entry["authorization"]["authorization_id"],entry["action"])
                        if job["authorization_binding"]!={"authorization_id":auth["authorization_id"],"action_hash":digest(entry["action"])}:
                            raise Rejected("SCHEDULE_AUTHORIZATION_MISMATCH")
                        if policy["decision"]=="STRONG_AUTH" and not s.auth.strong_valid(auth):
                            raise Rejected("STRONG_AUTH_REQUIRED")
                    except (Rejected,TypeError,KeyError) as exc:
                        job.update(status="REAUTH_REQUIRED",error=getattr(exc,"code","MISSING_AUTHORIZATION"))
                        self.save_job(job)
                        try:
                            results.append(self.refresh(job["task_id"],job["created_by"],job["action_id"])["status"])
                        except Rejected as refresh_error:
                            records=s.blackboard.get(job["task_id"])["action_records"]
                            current=next(row for row in records if row["action"]["action_id"]==job["action_id"])
                            current.update(status="BLOCKED",error=refresh_error.code)
                            self._save(job["task_id"],records,status="BLOCKED",errors=[refresh_error.code])
                            results.append("BLOCKED")
                    else:
                        results.append(self.execute(job["task_id"],job["action_id"],job)["status"])
        return {"processed":len(results),"statuses":results}

    def recover(self):
        s=self.s
        from backend.runtime.graph_claims import process_alive
        for item in s.db.rows("SELECT schedule_id FROM scheduled_jobs WHERE status IN ('CHECKING','EXECUTING','VERIFYING')"):
            refresh=False
            with s.db.transaction() as conn:
                row=conn.execute('SELECT * FROM scheduled_jobs WHERE schedule_id=?',(item['schedule_id'],)).fetchone()
                if row['status'] not in {'CHECKING','EXECUTING','VERIFYING'}:continue
                job=self._decode_job(row)
                claim=conn.execute('SELECT pid FROM graph_worker_claims WHERE task_id=?',(job['task_id'],)).fetchone()
                if claim and process_alive(claim['pid']):continue
                task=json.loads(conn.execute('SELECT body FROM tasks WHERE id=?',(job['task_id'],)).fetchone()['body'])
                refresh=job['status']=='CHECKING' and task['status']!='MANUAL_REVIEW'
                job['status']='REAUTH_REQUIRED' if refresh else 'MANUAL_REVIEW'
                conn.execute('UPDATE scheduled_jobs SET status=?,body=? WHERE schedule_id=?',(job['status'],self._encode_job(job),job['schedule_id']))
                if refresh:
                    # CHECKING is before execution. Releasing proven-dead ownership
                    # permits a new proposal and new user authorization, never payout.
                    conn.execute('DELETE FROM graph_worker_claims WHERE task_id=?',(job['task_id'],))
                else:
                    task.update(status='MANUAL_REVIEW',revision=task['revision']+1,updated_by='recovery',updated_at=time.time())
                    conn.execute('UPDATE tasks SET body=? WHERE id=?',(canonical(task),job['task_id']))
                s.audit.append_in(conn,job['task_id'],job['action_id'],'SCHEDULE_RECOVERED','recovery',{'schedule_id':job['schedule_id'],'status':job['status'],'bank_writes':0})
            if refresh:
                try:self.refresh(job['task_id'],job['created_by'],job['action_id'])
                except Rejected as exc:
                    if exc.code!='GRAPH_WORKER_BUSY':
                        job.update(status='MANUAL_REVIEW',error=exc.code);self.save_job(job)
                        s.blackboard.update(job['task_id'],'recovery',status='MANUAL_REVIEW')

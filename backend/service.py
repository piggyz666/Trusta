import json
import threading
import time
from backend.agents.supervisor import SupervisorAgent
from backend.agents.planner import PlannerAgent
from backend.agents.transaction import TransactionAgent
from backend.agents.finance import FinanceAgent
from backend.agents.wealth import WealthAgent
from backend.runtime.scenarios import ReadOnlyScenarios
from backend.audit import AuditEngine
from backend.authorization import AuthorizationEngine
from backend.banking.adapter import VirtualBankAdapter
from backend.banking.virtual_bank import VirtualBank
from backend.domain import Rejected, canonical, digest, uid
from backend.llm.configuration import LLMSettings
from backend.llm.contracts import InferenceContext, InferenceError, InferenceRequest
from backend.llm.gateway import LLMGateway
from backend.conversation.service import ConversationService
from backend.memory.store import MemoryStore
from backend.security.registry import CapabilityRegistry
from backend.security.keyring import MessageKeyRing
from backend.security.extensions import DisabledExtensions
from backend.execution.contract import ExecutionContractService
from backend.execution.gateway import ToolGateway
from backend.intent import IntentNormalization
from backend.persistence import Database
from backend.policy import PolicyEngine
from backend.receipts import ReceiptService
from backend.runtime.blackboard import Blackboard
from backend.runtime.message_bus import MessageBus
from backend.runtime.scheduler import Scheduler
from backend.schema_validator import SchemaValidator
from backend.security.capability import CapabilityManager
from backend.security.identity import AgentIdentity
from backend.security.replay_protection import ReplayProtection
from backend.security.signature import MessageSignature
from backend.verification import HardVerifier


class TrustaService:
    def __init__(self, config, recover_workflows=True, bank_provider=None):
        self.config = config
        if (type(config.finance_process_enabled) is not bool or type(config.agent_timeout_seconds) is not int
                or not 1 <= config.agent_timeout_seconds <= 120
                or config.finance_process_enabled and (config.banking_version != 5 or config.communication_version != 3)):
            raise Rejected('INVALID_AGENT_ISOLATION_PROFILE')
        if type(config.banking_version) is not int or config.banking_version not in {4,5} or config.banking_version==5 and config.communication_version!=3:
            raise Rejected("INVALID_BANKING_PROFILE")
        if type(config.communication_version) is not int or config.communication_version not in {2, 3}:
            raise Rejected("INVALID_COMMUNICATION_PROFILE")
        llm_settings = LLMSettings.load(config.llm_config_path) if config.llm_config_path else LLMSettings()
        self.db = Database(config.db_path)
        if bank_provider is None and config.bank_config_path:
            from backend.bank_sdk.configuration import reference_from_config
            bank_provider=reference_from_config(config.bank_config_path,self.db)
        if config.communication_version == 2 and self.db.one("SELECT id FROM crypto_trust_state WHERE id=1"):
            raise Rejected("MESSAGE_DOWNGRADE_DENIED")
        bank = VirtualBank(self.db,config.banking_version)
        if bank_provider is None:
            bank.seed()
        self.audit = AuditEngine(self.db)
        from backend.security.banking_controls import BankingControls
        self.safety = BankingControls(self.db, self.audit, config)
        self.bus = MessageBus(self.audit)
        self.blackboard = Blackboard(self.db)
        self.scheduler = Scheduler(self.db, self.bus)
        self.registry = CapabilityRegistry(config.banking_version)
        self.capabilities = CapabilityManager(self.registry)
        self.registry.validate()
        from backend.bank_sdk.bridge import CanonicalBankAdapter
        from backend.bank_sdk.virtual import VirtualBankProvider
        self.gateway = ToolGateway(CanonicalBankAdapter(bank_provider or VirtualBankProvider(VirtualBankAdapter(bank))), self.capabilities)
        self.schema = SchemaValidator(self.gateway)
        self.policy = PolicyEngine(self.db, self.gateway, config, self.safety)
        self.auth = AuthorizationEngine(self.db, config, self.safety)
        self.contracts = ExecutionContractService(self.db, self.policy, self.auth, self.capabilities, config)
        self.gateway.contracts, self.gateway.schema = self.contracts, self.schema
        self.gateway.safety = self.safety
        self.verifier = HardVerifier(self.gateway)
        self.receipts = ReceiptService(self.db, self.audit)
        self.intent = IntentNormalization()
        if config.banking_version==5:
            from backend.banking.intent import BankingIntent
            self.intent = BankingIntent(self.intent)
        self.supervisor = SupervisorAgent(self.blackboard)
        self.planner = PlannerAgent()
        self.transaction = TransactionAgent(self.gateway, config)
        self.finance = FinanceAgent(self.gateway, config,
                                    bank_provider.manifest.currencies if bank_provider else frozenset({'CNY'}),
                                    None if bank_provider else 'CNY')
        self.wealth = WealthAgent(self.gateway)
        self.scenarios = ReadOnlyScenarios(self)
        self.lock = threading.RLock()
        from backend.runtime.graph_claims import GraphClaims
        self.workflow_claims=GraphClaims(self)
        if recover_workflows:
            self.recover()
        elif not self.audit.verify_chain():
            raise Rejected("AUDIT_CHAIN_INVALID")
        self.message_keys = MessageKeyRing(self.db, self.audit, config.signing_key)
        self.messages = MessageSignature(AgentIdentity(config.signing_key, self.message_keys), ReplayProtection(self.db),
                                         lambda task_id: self.blackboard.get(task_id).get("revision", 0))
        self.crypto_registry = self.checkpoints = self.records = self.anchors = None
        if config.communication_version == 3:
            from backend.security.crypto_registry import CryptoIdentityRegistry
            from backend.security.message_v3 import TrustedMessageV3
            from backend.audit_trust.checkpoints import MerkleAudit
            from backend.audit_trust.records import RecordProofService
            from backend.audit_trust.anchor import AnchorWorker,configured_anchor
            self.crypto_registry = CryptoIdentityRegistry(self.db, self.audit, self.registry, config, self.lock)
            self.messages = TrustedMessageV3(self.crypto_registry, ReplayProtection(self.db),
                                            lambda task_id: self.blackboard.get(task_id).get("revision", 0),
                                            self._validate_message_context)
            self.capabilities.identity_guard = self.crypto_registry.require_active
            self.checkpoints = MerkleAudit(self.db, self.audit, self.crypto_registry, self.lock)
            self.records = RecordProofService(self.db, self.checkpoints, self._owned_task)
            from backend.runtime.handoff_evidence import HandoffEvidence
            self.handoffs = HandoffEvidence(self)
            anchor = configured_anchor(self.db,config)
            self.anchors = AnchorWorker(self.db, self.checkpoints, anchor)
        self.memory = MemoryStore(self.db, self.audit, self.lock)
        self.extensions = DisabledExtensions()
        self.did = None
        self.privacy = None
        if self.crypto_registry:
            from backend.security.did_key import DIDKeyBinding
            self.did=DIDKeyBinding(self.crypto_registry)
            from backend.security.extensions import Phase4Extensions
            self.extensions = Phase4Extensions(self)
        if config.zkp_enabled:
            from backend.security.privacy_proof import PrivacyProofService
            self.privacy=PrivacyProofService(self)
        self.llm = LLMGateway(llm_settings, self.db, self.audit)
        if self.crypto_registry:
            self.llm.identity_guard = self.crypto_registry.require_active
            self.memory.identity_guard = self.crypto_registry.require_active
        self.conversations = ConversationService(self)
        self.banking = None
        self.finance_transport = None
        if config.banking_version==5:
            from backend.agents.service import ServiceAgent
            from backend.runtime.banking import BankingRuntime
            self.service_agent = ServiceAgent(self.gateway,config)
            self.banking = BankingRuntime(self)
            from backend.banking.split import SplitCollection
            self.splits = SplitCollection(self)
            self.gateway.payment_guard = self.splits.check_action
            from backend.runtime.graph_runtime import GraphRuntime
            self.graphs = GraphRuntime(self)
            if config.finance_process_enabled:
                from backend.runtime.finance_transport import FinanceTransport
                self.finance_transport=FinanceTransport(self)
            from backend.runtime.event_plans import EventPlans
            self.events = EventPlans(self,recover=recover_workflows)
            from backend.banking.sandbox_feed import SandboxFeed
            if self.gateway._adapter.provider.manifest.provider=='virtual':
                self.feed = SandboxFeed(self)
            elif self.gateway.offers('transaction.list'):
                # Any other bank: incoming credits are read from its ordinary transaction list.
                from backend.banking.transaction_feed import BankTransactionFeed
                self.feed = BankTransactionFeed(self)
            else:
                self.feed = None
            from backend.automation.engine import AutomationEngine
            self.automation = AutomationEngine(self)
            if recover_workflows:
                self.banking.recover()

    def recover(self):
        if not self.audit.verify_chain():
            raise Rejected("AUDIT_CHAIN_INVALID", "审计链验证失败，停止启动")
        from backend.runtime.graph_claims import process_alive
        # Claim check, task/step recovery and its audit evidence commit together.
        # A second Service cannot race a worker acquiring ownership in between.
        for row in self.db.rows("SELECT id FROM tasks"):
            with self.db.transaction() as conn:
                task=json.loads(conn.execute('SELECT body FROM tasks WHERE id=?',(row['id'],)).fetchone()['body'])
                claim=conn.execute('SELECT pid FROM graph_worker_claims WHERE task_id=?',(row['id'],)).fetchone()
                if claim and process_alive(claim['pid']):continue
                if task['status'] not in {'EXECUTING','VERIFYING','RUNNING'}:continue
                uncertain=task['status'] in {'EXECUTING','VERIFYING'}
                target='MANUAL_REVIEW' if uncertain else 'FAILED'
                reason='RESTART_DURING_EXECUTION' if uncertain else 'RESTART_BEFORE_EXECUTION'
                for step in task['steps']:
                    if step['status']=='RUNNING':
                        step['status']=target
                        conn.execute('UPDATE task_steps SET status=? WHERE task_id=? AND step_id=?',(target,row['id'],step['step_id']))
                        self.audit.append_in(conn,row['id'],None,'STEP_RECOVERED','recovery',{'step_id':step['step_id'],'status':target})
                for record in task.get('action_records',[]):
                    if record['status'] in {'EXECUTING','VERIFYING'}:record.update(status='MANUAL_REVIEW',error=reason)
                task.update(status=target,errors=task['errors']+[reason],dag=task['steps'],
                            updated_by='recovery',updated_at=time.time(),revision=task['revision']+1)
                conn.execute('UPDATE tasks SET body=? WHERE id=?',(canonical(task),row['id']))
                self.audit.append_in(conn,row['id'],None,'MANUAL_REVIEW_REQUIRED' if uncertain else 'TASK_RECOVERED','recovery',{'reason':reason,'status':target})

    def _event(self, kind, task_id, actor, payload):
        return self.bus.publish(kind, task_id, None, actor, f"task:{task_id}", payload)

    def _agent_message(self, sender, receiver, task_id, step_id, kind, payload):
        reference = f"task:{task_id}:agent_results:{sender}:{kind}"
        message = self.messages.sign(sender, receiver, task_id, step_id, kind, reference, payload)
        if self.config.communication_version == 3:
            with self.lock:
                self._accept_message(message, payload, receiver, store_result=True)
            return message
        self.accept_message(message, payload, receiver)
        # The accepted-results area only receives a verified runtime handoff.
        task = self.blackboard.get(task_id)
        results = task["agent_results"]
        results[f"{sender}:{kind}"] = payload
        self.blackboard.update(task_id, sender, agent_results=results)

    def accept_message(self, message, payload, receiver):
        with self.lock:
            return self._accept_message(message, payload, receiver)

    def _accept_message(self, message, payload, receiver, store_result=False):
        try:
            if self.config.communication_version == 3:
                with self.db.transaction() as conn:
                    self.messages.verify_in(message, payload, receiver, conn)
                    self._validate_message_context(message, conn)
                    self.messages.replay.consume_in(conn, message["sender_agent"], message["nonce"], message["issued_at"] / 1000)
                    self.audit.append_in(conn, message["task_id"], None, "MESSAGE_ACCEPTED", receiver, message)
                    if store_result:
                        task=json.loads(conn.execute('SELECT body FROM tasks WHERE id=?',(message['task_id'],)).fetchone()['body'])
                        job=self.finance_transport.apply_in(conn,message,payload,task) if self.finance_transport else None
                        self.handoffs.record_in(conn,message,task,
                            input_hash=job['input_hash'] if job else None,process=job['process'] if job else None,
                            transport_request=job['request'] if job else None)
                        task['agent_results'][message['sender_agent']+':'+message['message_type']]=payload
                        task.update(revision=task['revision']+1,updated_by=message['sender_agent'],updated_at=time.time())
                        conn.execute('UPDATE tasks SET body=? WHERE id=?',(canonical(task),task['task_id']))
            else:
                self.messages.verify(message, payload, receiver, consume=False)
                self._validate_message_context(message)
                self.messages.replay.consume(message["sender_agent"], message["nonce"], message["timestamp"])
                self.audit.append(message["task_id"], None, "MESSAGE_ACCEPTED", receiver, message)
        except Rejected as exc:
            # This is the legacy trusted-process handoff API, not a public message ingress.
            if not isinstance(message, dict) or not isinstance(message.get("task_id"), str):
                raise
            if not self.db.one("SELECT id FROM tasks WHERE id=?", (message["task_id"],)):
                raise
            task = self.blackboard.get(message["task_id"])
            active = next((s for s in task["steps"] if s["status"] in {"READY", "RUNNING", "WAITING_USER"}), None)
            if active:
                self.scheduler.transition(task["task_id"], active["step_id"], "BLOCKED")
            self.blackboard.update(task["task_id"], "message_guard", status="BLOCKED", errors=task["errors"] + [exc.code])
            self.audit.append(task["task_id"], None, "MESSAGE_REJECTED", "message_guard", {"reason": exc.code})
            raise

    def _validate_message_context(self, message, conn=None):
        if conn is None:
            task = self.blackboard.get(message["task_id"])
        else:
            row = conn.execute("SELECT body FROM tasks WHERE id=?", (message["task_id"],)).fetchone()
            if not row:
                raise Rejected("TASK_NOT_FOUND")
            task = json.loads(row["body"])
        from backend.runtime.banking_plan import TASKS, OWNERS, initial_plan
        if task["task_type"]=="GENERAL_GRAPH":
            step=next((s for s in task["steps"] if s["step_id"]==message["step_id"]),None)
            if (not step or step["owner"]!=message["sender_agent"] or message["receiver_agent"]!="supervisor"
                    or message["message_type"] not in {"READ_ONLY_REPORT","ACTION_PROPOSED"} or step["status"]!="COMPLETED"
                    or message["payload_version"]!=task.get("revision",0)
                    or message["payload_ref"]!=f"task:{task['task_id']}:agent_results:{message['sender_agent']}:{message['message_type']}"):
                raise Rejected("MESSAGE_CONTEXT_DENIED")
            return
        if task["task_type"] in TASKS:
            if self.config.banking_version!=5:
                raise Rejected("MESSAGE_CONTEXT_DENIED")
            owner=OWNERS[task["task_type"]]
            edges={("supervisor","planner","REQUEST","R01"),("planner",owner,"PLAN","R01")}
            edges|={(step["owner"],"supervisor","READ_ONLY_REPORT",step["step_id"]) for step in initial_plan(task["task_type"]) if step["owner"] in {"finance","service","wealth","transaction"}}
            proposer = "service" if task["task_type"]=="CROSS_SCENARIO" else owner
            edges.add((proposer,"supervisor","ACTION_PROPOSED","R05" if task["task_type"]=="CROSS_SCENARIO" else "R03"))
            if (message["sender_agent"],message["receiver_agent"],message["message_type"],message["step_id"]) not in edges:
                raise Rejected("MESSAGE_CONTEXT_DENIED")
            if message["payload_version"]!=task.get("revision",0):
                raise Rejected("MESSAGE_STALE_PAYLOAD")
            if message["payload_ref"]!=f"task:{task['task_id']}:agent_results:{message['sender_agent']}:{message['message_type']}":
                raise Rejected("MESSAGE_REFERENCE_DENIED")
            return
        prefix, owner = {"TRANSFER": ("T", "transaction"), "BILL_ANALYSIS": ("F", "finance"),
                         "WEALTH_RECOMMENDATION": ("W", "wealth")}[task["task_type"]]
        edges = {("supervisor", "planner", "REQUEST", prefix + "01"),
                 ("planner", owner, "PLAN", prefix + "01")}
        edges.add((owner, "supervisor", "ACTION_PROPOSED" if prefix == "T" else "READ_ONLY_REPORT",
                   "T05" if prefix == "T" else prefix + "06"))
        if (message["sender_agent"], message["receiver_agent"], message["message_type"], message["step_id"]) not in edges:
            raise Rejected("MESSAGE_CONTEXT_DENIED")
        if message.get("schema_version") in {2, 3}:
            if message["payload_version"] != task.get("revision", 0):
                raise Rejected("MESSAGE_STALE_PAYLOAD")
            reference = f"task:{task['task_id']}:agent_results:{message['sender_agent']}:{message['message_type']}"
            if message["payload_ref"] != reference:
                raise Rejected("MESSAGE_REFERENCE_DENIED")

    def _step(self, task_id, step_id, fn):
        self.scheduler.start(task_id, step_id)
        result = fn()
        self.scheduler.transition(task_id, step_id, "COMPLETED")
        return result

    def _owned_task(self, task_id, user_id):
        task = self.blackboard.get(task_id)
        if task["user_id"] != user_id:
            raise Rejected("TASK_ACCESS_DENIED")
        return task

    def create_task(self, text, user_id="demo", source_account_id="acct-demo"):
        if self.banking:
            from backend.runtime.banking_plan import TASKS
            normalized=self.intent.normalize(text)
            if normalized["task_type"] in TASKS:
                return self.banking.create(text,user_id,source_account_id,normalized)
            source_account_id=normalized.get("source_account_id",source_account_id)
        if self.llm.settings.mode == "assisted":
            return self.create_normalized_task(text, user_id, source_account_id, self.intent.normalize(text))
        task = self._create_task(text, user_id, source_account_id)
        if self.llm.settings.mode == "shadow":
            self.shadow_task(task["task_id"], user_id)
            return self.get_task(task["task_id"], user_id)
        if task["status"] == "COMPLETED" and task.get("result"):
            self.assisted_presentation(task["task_id"], user_id)
            return self.get_task(task["task_id"], user_id)
        return task

    def cancel_draft(self, task_id, user_id):
        task=self._owned_task(task_id,user_id)
        if task['task_type']=='GENERAL_GRAPH' and task.get('graph_state'):
            return self.graphs.cancel(task_id,user_id)
        claims=self.banking.claims if self.banking and 'action_records' in task else self.workflow_claims
        with claims.hold(task_id,reentrant=True):
            return self._cancel_draft(task_id,user_id)

    def _cancel_draft(self, task_id, user_id):
        with self.lock:
            task = self._owned_task(task_id, user_id)
            if task['task_type']=='GENERAL_GRAPH' and task.get('graph_state'):
                return self.graphs.cancel(task_id,user_id)
            if self.banking and "action_records" in task:
                if task["status"]=="MANUAL_REVIEW":
                    return False
                changed=False
                if not task["action_records"] and task["status"]=="WAITING_USER":
                    for step in task["steps"]:
                        if step["status"]=="WAITING_USER":
                            self.scheduler.transition(task_id,step["step_id"],"CANCELLED")
                    self.blackboard.update(task_id,"runtime",status="CANCELLED")
                    return True
                for record in task["action_records"]:
                    if record["status"]=="WAITING_USER" and record["authorization"]:
                        self.banking.authorize(task_id,user_id,digest(record["action"]),False)
                        changed=True
                    elif record["status"]=="SCHEDULED":
                        record["status"]="DENIED"
                        self.banking._cancel_branch(task_id,record["prefix"])
                        self.banking._save(task_id,task["action_records"])
                        for job in self.banking.jobs(task_id):
                            if job["action_id"]==record["action"]["action_id"]:
                                job["status"]="CANCELLED"
                                self.banking.save_job(job)
                        self.banking.aggregate(task_id)
                        changed=True
                return changed
            if task["status"] not in {"PENDING", "WAITING_USER"}:
                return False
            if task["authorization"]:
                auth = self.auth.decide(task["authorization"]["authorization_id"], user_id,
                                       task["proposed_actions"][0], False)
                self.blackboard.update(task_id, "authorization", authorization=auth)
                self._event("AUTHORIZATION_DENIED", task_id, "authorization", auth)
            waiting = next((step for step in task["steps"] if step["status"] == "WAITING_USER"), None)
            if waiting:
                self.scheduler.transition(task_id, waiting["step_id"], "CANCELLED")
            self.blackboard.update(task_id, "runtime", status="CANCELLED")
            self.audit.append(task_id, None, "DRAFT_CANCELLED", "runtime", {"reason": "EXPLICIT_CONVERSATION_CHANGE"})
            return True

    def create_normalized_task(self, text, user_id, source_account_id, normalized,
                               conversation_id=None, turn_id=None, conversation_revision=None, current_check=None):
        from backend.runtime.banking_plan import TASKS
        if self.banking and normalized["task_type"] in TASKS:
            if current_check:
                current_check()
            return self.banking.create(text,user_id,source_account_id,normalized)
        with self.lock:
            if current_check:
                current_check()
            task_id = self.supervisor.receive(user_id, text, normalized)
            if conversation_id:
                self.blackboard.update(task_id, "runtime", conversation_id=conversation_id, turn_id=turn_id)
                self.conversations.store.bind_task(conversation_id, user_id, turn_id, conversation_revision, task_id)
            task = self.blackboard.get(task_id)
            context = InferenceContext(task_id, user_id, "planner", task["task_type"], task["revision"])
        plan = self.planner.plan(task)
        if self.llm.settings.mode == "assisted" and not normalized.get("needs_clarification"):
            try:
                candidate = self.planner.propose_template(context, self.llm.infer)
            except InferenceError:
                candidate = None
            with self.lock:
                if current_check:
                    current_check()
                if candidate and candidate.status == "SUCCEEDED":
                    accepted = self.blackboard.attach_candidate(task_id, candidate.request_id, user_id,
                                                                 context.revision, candidate.output, self.audit)
                    if not accepted:
                        self.cancel_draft(task_id, user_id)
                        return self.blackboard.get(task_id)
                    # Instantiation remains deterministic; the model only chooses the verified template.
                    plan = self.planner.plan(self.blackboard.get(task_id))
                else:
                    self.audit.append(task_id, None, "LLM_PLAN_FALLBACK", "planner", {"reason": candidate.error_code if candidate else "LLM_UNAVAILABLE"})
        with self.lock:
            if current_check:
                current_check()
            task = self._create_task(text, user_id, source_account_id, normalized, task_id, plan)
        if task["status"] == "COMPLETED" and task["result"]:
            self.assisted_presentation(task_id, user_id)
        return self.get_task(task_id, user_id)

    def assisted_presentation(self, task_id, user_id):
        with self.lock:
            task = self._owned_task(task_id, user_id)
            if task["status"] != "COMPLETED" or not task["result"]:
                return
            report = task["result"]
            if task['task_type']=='BILL_ANALYSIS' and report.get('period')=='ANNUAL':
                # Annual reports use deterministic facts until an annual-specific
                # model projection is independently validated.
                return
            if task["task_type"] == "BILL_ANALYSIS":
                actor, purpose = "finance", "finance_presentation"
                flags = report["anomalies"]["flags"]
                payload = {"month": report["month"], "net_spend_cents": report["summary"]["net_spend"],
                           "settled_count": report["summary"]["settled_count"],
                           "large_count": sum("LARGE_PURCHASE" in f["rule_ids"] for f in flags),
                           "duplicate_count": sum("POSSIBLE_DUPLICATE" in f["rule_ids"] for f in flags),
                           "evidence_refs": ["monthly_summary", "anomaly_summary"]}
                presenter = self.finance.present
            else:
                actor, purpose = "wealth", "wealth_presentation"
                products = [{k: p[k] for k in ("product_id", "risk_level", "term_days", "minimum_amount")}
                            for p in report["recommendations"]]
                payload = {"risk_level": report["customer_risk_level"], "products": products,
                           "evidence_refs": ["risk_profile"] + ["product:" + p["product_id"] for p in products]}
                presenter = self.wealth.present
            context = InferenceContext(task_id, user_id, actor, task["task_type"], task["revision"])
        result = None
        if self.llm.settings.mode == "assisted":
            try:
                result = self.llm.infer(InferenceRequest(uid(), context, purpose, payload))
            except InferenceError:
                result = None
        with self.lock:
            accepted = False
            if result and result.status == "SUCCEEDED":
                accepted = self.blackboard.attach_candidate(task_id, result.request_id, user_id, context.revision, result.output, self.audit)
                if not accepted:
                    return
            elif self.llm.settings.mode == "assisted":
                self.audit.append(task_id, None, "LLM_PRESENTATION_FALLBACK", actor,
                                  {"reason": result.error_code if result else "LLM_UNAVAILABLE"})
            latest = self._owned_task(task_id, user_id)
            if latest["status"] != "COMPLETED" or (not accepted and latest.get("revision", 0) != context.revision):
                return
            # Re-read consent/TTL AFTER inference. Memory never enters provider prompts or intent slots.
            memories = self.memory.read(actor, user_id, "PREFERENCE")
            memories = [entry for entry in memories if entry["key"] == "explanation_style" or actor == "finance" and entry["key"] == "finance_focus"]
            if not accepted and not memories:
                return
            candidate = json.loads(canonical(result.output)) if accepted else (
                {"focus": "monthly_summary", "style": "brief"} if actor == "finance" else
                {"products": [{"product_id": p["product_id"], "reason_codes": ["RISK_MATCH", "KNOWN_TERM"]} for p in report["recommendations"]], "style": "brief"})
            preferences = {e["key"]: e["value"] for e in memories}
            candidate["style"] = preferences.get("explanation_style", candidate["style"])
            if actor == "finance" and preferences.get("finance_focus"):
                candidate["focus"] = preferences["finance_focus"] if report["anomalies"]["flags"] else "monthly_summary"
            presentation = presenter(report, candidate)
            presentation.update(source="LLM_ASSISTED_TEMPLATE" if accepted else "MEMORY_TEMPLATE",
                                request_id=result.request_id if accepted else None,
                                memory_refs=[{k: entry[k] for k in ("memory_id", "revision", "expires_at")} for entry in memories])
            self.blackboard.update(task_id, "runtime", presentation=presentation)
            self.audit.append(task_id, None, "GROUNDED_PRESENTATION_PUBLISHED", actor, presentation)

    def remember_risk_reference(self, user_id, consent, ttl_seconds=MemoryStore.DEFAULT_TTL):
        with self.lock:
            self.memory._validate_consent(consent, ttl_seconds)
            return self.memory.risk_reference(user_id, self.wealth.risk_profile(user_id), consent, ttl_seconds)

    def security_status(self):
        with self.lock:
            if self.crypto_registry:
                return {"registry": self.registry.snapshot(), "messages": self.crypto_registry.snapshot(),
                        "extensions": self.extensions.status()}
            return {"registry": self.registry.snapshot(), "messages": self.message_keys.status(),
                    "extensions": self.extensions.status()}

    def shadow_task(self, task_id, user_id="demo"):
        """Runtime creates context/projection. Slow inference never holds the service lock."""
        with self.lock:
            task = self._owned_task(task_id, user_id)
            if task["status"] not in {"WAITING_USER", "COMPLETED"}:
                return None
            purpose, actor, payload = "intent_normalization", "supervisor", {"text": task["user_goal"]}
            if task["result"] and task["task_type"] == "BILL_ANALYSIS":
                if task['result'].get('period')=='ANNUAL':
                    return None
                purpose, actor = "finance_explanation", "finance"
                payload = {"month": task["result"]["month"], "net_spend_cents": task["result"]["summary"]["net_spend"],
                           "settled_count": task["result"]["summary"]["settled_count"], "evidence_refs": ["monthly_summary"]}
            elif task["result"] and task["task_type"] == "WEALTH_RECOMMENDATION":
                purpose, actor = "wealth_explanation", "wealth"
                products = [{k: p[k] for k in ("product_id", "risk_level", "term_days", "minimum_amount")}
                            for p in task["result"]["recommendations"]]
                payload = {"risk_level": task["result"]["customer_risk_level"], "products": products,
                           "evidence_refs": ["risk_profile"] + ["product:" + p["product_id"] for p in products]}
            context = InferenceContext(task_id, user_id, actor, task["task_type"], task.get("revision", 0))
            request = InferenceRequest(uid(), context, purpose, payload)
        try:
            result = self.llm.infer(request)
        except InferenceError as exc:
            self.audit.append(task_id, None, "LLM_SHADOW_REJECTED", "runtime", {"reason": exc.code})
            return None
        if result.status == "SUCCEEDED":
            with self.lock:
                self.blackboard.attach_candidate(task_id, result.request_id, user_id, context.revision, result.output, self.audit)
        return result

    def _create_task(self, text, user_id="demo", source_account_id="acct-demo", normalized=None, task_id=None, plan=None):
        with self.lock:
            normalized=normalized if normalized is not None else self.intent.normalize(text)
            if task_id is None:task_id=self.supervisor.receive(user_id,text,normalized)
        with self.workflow_claims.hold(task_id,reentrant=True):
            return self._create_claimed_legacy(text,user_id,source_account_id,normalized,task_id,plan)

    def _create_claimed_legacy(self, text, user_id, source_account_id, normalized, task_id, plan):
        with self.lock:
            normalized = normalized if normalized is not None else self.intent.normalize(text)
            if task_id:
                existing = self._owned_task(task_id, user_id)
                if existing["status"] != "PENDING":
                    return existing
                if existing["context"]["normalized"] != normalized:
                    raise Rejected("INTENT_CHANGED")
            else:
                task_id = self.supervisor.receive(user_id, text, normalized)
            self.blackboard.update(task_id, "runtime", status="RUNNING")
            self._event("TASK_CREATED", task_id, "supervisor", normalized)
            try:
                prefix = {"TRANSFER": "T", "BILL_ANALYSIS": "F", "WEALTH_RECOMMENDATION": "W"}[normalized["task_type"]]
                self._agent_message("supervisor", "planner", task_id, prefix + "01", "REQUEST", normalized)
                plan = plan if plan is not None else self.planner.plan(self.blackboard.get(task_id))
                owner = {"TRANSFER": "transaction", "BILL_ANALYSIS": "finance", "WEALTH_RECOMMENDATION": "wealth"}[normalized["task_type"]]
                self._agent_message("planner", owner, task_id, prefix + "01", "PLAN", plan)
                self.blackboard.install_plan(task_id, plan)
                if normalized["task_type"] != "TRANSFER":
                    return self.scenarios.run(task_id, source_account_id)
                self.scheduler.start(task_id, "T01")
                if normalized.get("needs_clarification"):
                    self.scheduler.transition(task_id, "T01", "WAITING_USER")
                    return self.blackboard.update(task_id, "supervisor", status="WAITING_USER", clarification=normalized["clarification"])
                self.scheduler.transition(task_id, "T01", "COMPLETED")
                recipient = self._step(task_id, "T02", lambda: self.transaction.recipient(user_id, normalized["recipient_name"]))
                account = self._step(task_id, "T03", lambda: self.transaction.account(user_id, source_account_id))
                balance = self._step(task_id, "T04", lambda: self.transaction.balance(user_id, source_account_id))
                context = {"normalized": normalized, "recipient": recipient, "source_account": account, "balance": balance}
                self.blackboard.update(task_id, "transaction", context=context)
                action = self._step(task_id, "T05", lambda: self.transaction.propose(self.blackboard.get(task_id), recipient, account, balance))
                with self.db.transaction() as db:
                    db.execute("INSERT INTO actions VALUES(?,?)", (action["action_id"], canonical(action)))
                self.blackboard.update(task_id, "transaction", proposed_actions=[action])
                self._agent_message("transaction", "supervisor", task_id, "T05", "ACTION_PROPOSED", action)
                self._event("ACTION_PROPOSED", task_id, "transaction", action)
                self.schema.validate(action)
                self.capabilities.check("transaction", "transfer.propose")
                self.capabilities.check("executor", action["requested_capability"])
                policy = self._step(task_id, "T06", lambda: self.policy.evaluate(action))
                self.blackboard.update(task_id, "policy", policy_decision=policy)
                if policy["decision"] == "BLOCK":
                    self.scheduler.transition(task_id, "T07", "BLOCKED")
                    return self.blackboard.update(task_id, "policy", status="BLOCKED", errors=policy["reason_codes"])
                auth = self.auth.request(action, policy)
                self.scheduler.start(task_id, "T07")
                self.scheduler.transition(task_id, "T07", "WAITING_USER")
                self.blackboard.update(task_id, "authorization", status="WAITING_USER", authorization=auth)
                self._event("AUTHORIZATION_REQUIRED", task_id, "authorization", auth)
            except Rejected as exc:
                task = self.blackboard.get(task_id)
                running = next((s for s in task["steps"] if s["status"] == "RUNNING"), None)
                waiting = exc.code == "RECIPIENT_AMBIGUOUS"
                if running:
                    self.scheduler.transition(task_id, running["step_id"], "WAITING_USER" if waiting else "BLOCKED")
                self.blackboard.update(task_id, "runtime", status="WAITING_USER" if waiting else "BLOCKED",
                                       errors=task["errors"] + [exc.code], clarification=str(exc) if waiting else None)
            except Exception:
                for step in self.blackboard.get(task_id)['steps']:
                    if step['status']=='RUNNING':self.scheduler.transition(task_id,step['step_id'],'FAILED')
                self.blackboard.update(task_id,'runtime',status='FAILED',errors=['BANKING_READ_UNCERTAIN'])
                self.audit.append(task_id,None,'BANKING_TASK_REJECTED','runtime',{'reason':'BANKING_READ_UNCERTAIN'})
            return self.blackboard.get(task_id)

    def authorize(self, task_id, user_id, action_hash, approve, strong_secret=""):
        if self.banking and "action_records" in self._owned_task(task_id,user_id):
            return self.banking.authorize(task_id,user_id,action_hash,approve,strong_secret)
        with self.workflow_claims.hold(task_id,reentrant=True), self.lock:
            task = self._owned_task(task_id, user_id)
            if task["status"] != "WAITING_USER" or not task["authorization"]:
                raise Rejected("TASK_NOT_AWAITING_AUTHORIZATION")
            action = task["proposed_actions"][0]
            if action_hash != digest(action):
                raise Rejected("AUTHORIZATION_HASH_MISMATCH")
            if self.config.banking_version == 5 and approve:
                current_policy = self.policy.evaluate(action)
                if current_policy["decision"] == "STRONG_AUTH" and task["authorization"]["auth_method"] not in ({"totp"} if self.safety.enabled else {"sandbox_secret"}):
                    new_auth = self.auth.request(action,current_policy)
                    return self.blackboard.update(task_id,"authorization",policy_decision=current_policy,authorization=new_auth,
                                                  clarification="风险要求已提高，请核对动作并重新进行强化授权。")
            auth = self.auth.decide(task["authorization"]["authorization_id"], user_id, action, approve, strong_secret)
            self.blackboard.update(task_id, "authorization", authorization=auth)
            if auth["status"] == "EXPIRED":
                self._event("AUTHORIZATION_EXPIRED", task_id, "authorization", auth)
                return self.blackboard.get(task_id)
            if auth["status"] == "DENIED":
                self.scheduler.transition(task_id, "T07", "CANCELLED")
                self.blackboard.update(task_id, "authorization", status="CANCELLED")
                self._event("AUTHORIZATION_DENIED", task_id, "authorization", auth)
                return self.blackboard.get(task_id)
            self._event("AUTHORIZATION_GRANTED", task_id, "authorization", auth)
            self.scheduler.start(task_id, "T07")
            self.scheduler.transition(task_id, "T07", "COMPLETED")
            return self._execute(task_id, action)

    def renew_authorization(self, task_id, user_id):
        with self.workflow_claims.hold(task_id,reentrant=True), self.lock:
            task = self._owned_task(task_id, user_id)
            if task["status"] != "WAITING_USER" or not task["authorization"]:
                raise Rejected("TASK_NOT_AWAITING_AUTHORIZATION")
            action = task["proposed_actions"][0]
            if time.time() >= action["expires_at"]:
                raise Rejected("ACTION_EXPIRED", "动作已过期，请重新提交转账请求")
            old = self.auth.get(task["authorization"]["authorization_id"])
            if time.time() < old["expires_at"] and old["status"] != "EXPIRED":
                raise Rejected("AUTHORIZATION_NOT_EXPIRED")
            policy = self.policy.evaluate(action)
            if policy["decision"] == "BLOCK":
                self.scheduler.transition(task_id, "T07", "BLOCKED")
                return self.blackboard.update(task_id, "policy", status="BLOCKED", policy_decision=policy, errors=policy["reason_codes"])
            auth = self.auth.request(action, policy)
            self.blackboard.update(task_id, "authorization", policy_decision=policy, authorization=auth)
            self._event("AUTHORIZATION_REQUIRED", task_id, "authorization", auth)
            return self.blackboard.get(task_id)

    def _execute(self, task_id, action):
        task = self.blackboard.get(task_id)
        try:
            # All pre-write rejection happens before EXECUTION_STARTED.
            self.schema.validate(action)
            if self.config.banking_version == 5:
                policy = self.policy.evaluate(action)
                if policy["decision"] == "BLOCK":
                    self.scheduler.start(task_id,"T08")
                    self.scheduler.transition(task_id,"T08","FAILED")
                    self._event("EXECUTION_FAILED",task_id,"executor",{"reason":"POLICY_CHANGED","reason_codes":policy["reason_codes"]})
                    return self.blackboard.update(task_id,"executor",status="FAILED",errors=policy["reason_codes"])
                self.blackboard.update(task_id,"policy",policy_decision=policy)
                task = self.blackboard.get(task_id)
            contract = self.contracts.issue(action, task["policy_decision"]["policy_decision_id"], task["authorization"]["authorization_id"])
            self.scheduler.start(task_id, "T08")
            self.blackboard.update(task_id, "executor", status="EXECUTING", execution={"contract": contract})
            self._event("EXECUTION_STARTED", task_id, "executor", {"action_hash": digest(action)})
        except Rejected as exc:
            step = next(s for s in self.blackboard.get(task_id)["steps"] if s["step_id"] == "T08")
            if step["status"] in {"PENDING", "READY", "RUNNING"}:
                self.scheduler.transition(task_id, "T08", "BLOCKED")
            self.blackboard.update(task_id, "executor", status="BLOCKED", errors=[exc.code])
            return self.blackboard.get(task_id)
        try:
            transaction = self.gateway.execute("executor", action, contract)
        except Rejected as exc:
            # VirtualBank explicit domain errors guarantee a rolled-back transaction.
            self.scheduler.transition(task_id, "T08", "FAILED")
            self.blackboard.update(task_id, "executor", status="FAILED", errors=[exc.code])
            self._event("EXECUTION_FAILED", task_id, "executor", {"reason": exc.code})
            return self.blackboard.get(task_id)
        except Exception:
            self.scheduler.transition(task_id, "T08", "MANUAL_REVIEW")
            self.blackboard.update(task_id, "executor", status="MANUAL_REVIEW", errors=["EXECUTION_RESULT_UNCERTAIN"])
            self._event("MANUAL_REVIEW_REQUIRED", task_id, "executor", {"reason": "EXECUTION_RESULT_UNCERTAIN"})
            return self.blackboard.get(task_id)
        self.blackboard.update(task_id, "executor", status="VERIFYING", execution={"contract": contract, "transaction": transaction})
        self.scheduler.transition(task_id, "T08", "COMPLETED")
        self._event("EXECUTION_COMPLETED", task_id, "executor", {"transaction_id": transaction["id"]})
        self.scheduler.start(task_id, "T09")
        try:
            verification = self.verifier.verify(action, transaction["id"])
            self.safety.verification_result(action['customer_id'],verification)
        except Exception:
            self.scheduler.transition(task_id,'T09','MANUAL_REVIEW')
            self.blackboard.update(task_id,'verifier',status='MANUAL_REVIEW',errors=['VERIFICATION_RESULT_UNCERTAIN'])
            self._event('MANUAL_REVIEW_REQUIRED',task_id,'verifier',{'reason':'VERIFICATION_RESULT_UNCERTAIN'})
            return self.blackboard.get(task_id)
        self.blackboard.update(task_id, "verifier", verification=verification)
        if verification["status"] != "VERIFIED":
            self.scheduler.transition(task_id, "T09", "MANUAL_REVIEW")
            self.blackboard.update(task_id, "verifier", status="MANUAL_REVIEW")
            self._event("VERIFICATION_FAILED", task_id, "verifier", verification)
            self._event("MANUAL_REVIEW_REQUIRED", task_id, "verifier", verification)
            return self.blackboard.get(task_id)
        self.scheduler.transition(task_id, "T09", "COMPLETED")
        self._event("VERIFICATION_COMPLETED", task_id, "verifier", verification)
        try:
            receipt = self._step(task_id, "T10", lambda: self.receipts.create(action, contract, transaction, verification))
        except Exception:
            step=next(s for s in self.blackboard.get(task_id)['steps'] if s['step_id']=='T10')
            if step['status']=='RUNNING':self.scheduler.transition(task_id,'T10','MANUAL_REVIEW')
            self.blackboard.update(task_id,'runtime',status='MANUAL_REVIEW',errors=['RECEIPT_PERSISTENCE_UNCERTAIN'])
            self._event('MANUAL_REVIEW_REQUIRED',task_id,'receipts',{'reason':'RECEIPT_PERSISTENCE_UNCERTAIN'})
            return self.blackboard.get(task_id)
        self.blackboard.update(task_id, "receipts", receipt=receipt, status="COMPLETED", audit_refs=[receipt["audit_ref"]])
        return self.blackboard.get(task_id)

    def get_task(self, task_id, user_id="demo"):
        with self.lock:
            task = self._owned_task(task_id, user_id)
            if self.banking and "action_records" in task:
                task["scheduled_jobs"] = self.banking.jobs(task_id)
                return task
            auth = task.get("authorization")
            if task["status"] == "WAITING_USER" and auth and auth["status"].startswith("PENDING") and time.time() >= auth["expires_at"]:
                try:
                    with self.workflow_claims.hold(task_id,reentrant=True):
                        task=self._owned_task(task_id,user_id);auth=task.get('authorization')
                        if task['status']=='WAITING_USER' and auth and auth['status'].startswith('PENDING') and time.time()>=auth['expires_at']:
                            auth=self.auth.decide(auth['authorization_id'],user_id,task['proposed_actions'][0],False)
                            self.blackboard.update(task_id,'authorization',authorization=auth)
                            self._event('AUTHORIZATION_EXPIRED',task_id,'authorization',auth)
                        task=self.blackboard.get(task_id)
                except Rejected as exc:
                    if exc.code!='GRAPH_WORKER_BUSY':raise
                    task=self.blackboard.get(task_id)  # Read only while another instance owns a transition.
            return task

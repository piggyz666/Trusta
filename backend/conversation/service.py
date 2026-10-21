import re
import time
from backend.conversation.schemas import NUMBER, SUFFIX, control, extract, normalize_fields
from backend.conversation.store import ConversationStore, empty_slots
from backend.domain import Rejected
from backend.llm.contracts import InferenceContext, InferenceError


class ConversationService:
    """Dialogue coordinator. Only the existing runtime may create/execute bank proposals."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = ConversationStore(runtime.db, runtime.audit)

    def create(self, user_id="demo"):
        with self.runtime.lock:
            return self.store.create(user_id)

    def get(self, conversation_id, user_id="demo"):
        with self.runtime.lock:
            return self.store.get(conversation_id, user_id)

    def close(self, conversation_id, user_id="demo"):
        with self.runtime.lock:
            body = self.store.get(conversation_id, user_id)
            self._cancel(body, user_id)
            self.store.close(conversation_id, user_id)
            if body["active_turn"]:
                self.store.abort(conversation_id, user_id, body["active_turn"], "CONVERSATION_CLOSED")
            return self.store.get(conversation_id, user_id)

    def _cancel(self, body, user_id):
        task_id = body.get("current_task_id")
        if task_id:
            return self.runtime.cancel_draft(task_id, user_id)

    def _hydrate(self, response, user_id):
        return response | {"task": self.runtime.get_task(response["task_id"], user_id) if response.get("task_id") else None}

    def send(self, conversation_id, user_id, turn_id, revision, text, source_account_id="acct-demo"):
        s = self.runtime
        with s.lock:
            existing = self.store.get(conversation_id, user_id)
            if existing["active_turn"]:
                turn = next(t for t in existing["turns"] if t["turn_id"] == existing["active_turn"])
                if turn["deadline"] <= time.time():
                    self._cancel(existing, user_id)
                    self.store.abort(conversation_id, user_id, turn["turn_id"], "TURN_EXPIRED")
            body, replay = self.store.claim(conversation_id, user_id, turn_id, revision, text, source_account_id)
            if replay:
                return self._hydrate(replay, user_id)
        task_id = None
        try:
            return self._send(body, user_id, turn_id, text, source_account_id)
        except Exception as exc:
            with s.lock:
                current = self.store.get(conversation_id, user_id)
                turn = next(t for t in current["turns"] if t["turn_id"] == turn_id)
                task_id = turn.get("task_id")
                if task_id:
                    s.cancel_draft(task_id, user_id)
                self.store.abort(conversation_id, user_id, turn_id,
                                 exc.code if isinstance(exc, (Rejected, InferenceError)) else "CONVERSATION_FAILED")
            if isinstance(exc, (Rejected, InferenceError)):
                raise Rejected(exc.code) from None
            raise Rejected("CONVERSATION_FAILED") from None

    def _send(self, body, user_id, turn_id, text, source_account_id):
        s, conversation_id, revision = self.runtime, body["conversation_id"], body["revision"]
        def finish(status, message, slots=None, sources=None, **extra):
            with s.lock:
                response = self.store.finish(conversation_id, user_id, turn_id, revision,
                                              {"status": status, "message": message, "task_id": None} | extra,
                                              slots or empty_slots(), sources)
                return self._hydrate(response, user_id)
        # Existing graph planner is the composition path. Chat still cannot
        # approve any graph action or infer financial literals from context.
        if s.banking and re.search(r'然后|接着|顺便|并且|；|;|^等我确认后|^如果我选择继续',text):
            from backend.llm.grounded_request import normalize_request, GroundedRequestPlanner
            try: plan=normalize_request(s,text,source_account_id)
            except Rejected as exc:
                if exc.code in {'NL_CLARIFICATION_REQUIRED','NL_EXPLICIT_REQUEST_REQUIRED','NL_UNSUPPORTED_COMPOSITION'}:
                    return finish('CLARIFICATION','请补充完整的每项操作及参数；条件选择和聊天文字都不构成金融授权。',reason=exc.code)
                raise
            def composition_current():return self.store.assert_current(conversation_id,user_id,turn_id,revision)
            def bind(task_id):
                composition_current();self.store.bind_task(conversation_id,user_id,turn_id,revision,task_id)
                s.blackboard.update(task_id,'runtime',conversation_id=conversation_id,turn_id=turn_id)
            with s.lock:
                composition_current();self._cancel(body,user_id)
            if s.llm.settings.mode=='assisted':
                task=GroundedRequestPlanner(s).propose(user_id,text,source_account_id,on_task_created=bind,current_check=composition_current)
            else:
                with s.lock:
                    composition_current();task_id=s.supervisor.receive(user_id,text,{'task_type':'GENERAL_GRAPH','needs_clarification':False})
                    bind(task_id);expected=s.blackboard.get(task_id)['revision']
                task=s.graphs.create(user_id,text,plan['graph'],plan['inputs'],task_id,expected_revision=expected)
            return finish(task['status'],'组合任务已处理；每笔金融动作继续逐项核对和授权。',task_id=task['task_id'])
        if s.banking and control(text) not in {"CANCEL","AMBIGUOUS"}:
            from backend.runtime.banking_plan import TASKS
            normalized=s.intent.normalize(text)
            goal=text
            if body.get("current_task_id"):
                previous_task=s._owned_task(body["current_task_id"],user_id)
                if previous_task["task_type"] in TASKS and previous_task["status"]=="WAITING_USER" and not previous_task.get("action_records"):
                    combined=s.intent.normalize(previous_task["user_goal"]+" "+text)
                    if combined["task_type"] in TASKS and not combined.get("needs_clarification"):
                        normalized,goal=combined,previous_task["user_goal"]+" "+text
            if normalized["task_type"] in TASKS:
                self.store.assert_current(conversation_id,user_id,turn_id,revision)
                self._cancel(body,user_id)
                task=s.banking.create(goal,user_id,source_account_id,normalized)
                self.store.bind_task(conversation_id,user_id,turn_id,revision,task["task_id"])
                s.blackboard.update(task["task_id"],"runtime",conversation_id=conversation_id,turn_id=turn_id)
                return finish("CLARIFICATION" if normalized.get("needs_clarification") else "TASK_CREATED",
                              task.get("clarification") or "任务已创建；每笔写动作需要独立核对和授权。",task_id=task["task_id"])
        command = control(text)
        if command:
            if command == "CANCEL":
                with s.lock:
                    self.store.assert_current(conversation_id, user_id, turn_id, revision)
                    cancelled = self._cancel(body, user_id)
                message = "已取消待处理请求；没有执行新的资金操作。" if cancelled else "已清除待补充信息；已执行交易不会被撤销。"
                return finish("CANCELLED", message)
            message = {"AUTHORIZATION": "聊天文字不能批准转账。请核对授权卡并通过确认按钮审批。",
                       "FORBIDDEN": "当前只支持理财推荐，禁止购买和赎回。请一次提交一个支持的场景。",
                       "AMBIGUOUS": "这条请求包含否定、条件或示例，请明确是否要创建任务，并提供完整信息。"}[command]
            if command == "AMBIGUOUS" and re.search(r"不要|不想|别转|不转", text):
                with s.lock:
                    self.store.assert_current(conversation_id, user_id, turn_id, revision)
                    self._cancel(body, user_id)
                return finish("CLARIFICATION", message)
            return finish("CLARIFICATION", message, body["slots"], body.get("slot_sources"),
                          task_id=body.get("current_task_id") if command == "AUTHORIZATION" else None)
        previous, sources = body["slots"], dict(body.get("slot_sources", {}))
        edit = re.fullmatch(rf"(?:金额)?(?:改成|改为)\s*(?P<amount>{NUMBER})\s*(?:元|人民币|块钱?)" + SUFFIX, text.strip())
        if edit:
            with s.lock:
                self.store.assert_current(conversation_id, user_id, turn_id, revision)
                if not body.get("current_task_id"):
                    return finish("CLARIFICATION", "没有可修改的待审批转账，请重新提供收款人与金额。")
                old = s._owned_task(body["current_task_id"], user_id)
                if old['task_type']=='GENERAL_GRAPH':
                    return finish('CLARIFICATION','请提供修改后的完整组合请求；聊天金额不能改写既有图中的授权。',task_id=old['task_id'])
                if old["status"] != "WAITING_USER" or not old["proposed_actions"]:
                    return finish("CLARIFICATION", "该动作已不处于待审批状态，请重新提供完整请求。")
                previous = empty_slots() | {"task_type": "TRANSFER", "recipient_text": old["context"]["normalized"]["recipient_name"]}
                sources = dict(old["context"]["normalized"].get("input_evidence", {}))
                self._cancel(body, user_id)
                source_account_id = old["proposed_actions"][0]["source_account_id"]
                edit_currency=old['proposed_actions'][0]['currency']
            # The changed amount is current explicit user input; only verified recipient context is carried.
            text_for_intent = edit["amount"] + "元"
        else:
            text_for_intent = text
        fields = extract(text_for_intent, previous)
        if fields["task_type"] == "UNKNOWN":
            return finish("CLARIFICATION", "请一次提供一个明确场景，例如：给张三转2000元、分析2026-09消费、推荐适合我的理财产品。")
        for key in ("recipient_text", "amount_text", "month_text"):
            if fields.get(key) and fields[key] in text:
                sources[key] = f"conversation:{conversation_id}:turn:{turn_id}"
        # An explicit replacement invalidates the earlier approval before any slow inference.
        if fields["task_type"] == "TRANSFER" and body.get("current_task_id") and not edit:
            with s.lock:
                self.store.assert_current(conversation_id, user_id, turn_id, revision)
                self._cancel(body, user_id)
        call = None
        try:
            canonical = s.intent.normalize(text_for_intent)
        except Rejected:
            canonical = {"needs_clarification": True}
        canonical_complete = not canonical["needs_clarification"] and not edit and previous["task_type"] == "UNKNOWN"
        if s.llm.settings.mode == "assisted" and not canonical_complete:
            context = InferenceContext(None, user_id, "supervisor", "CONVERSATION", revision, conversation_id)
            try:
                call = s.supervisor.propose_intent(context, {"text": text_for_intent, "slots": previous}, s.llm.infer)
            except InferenceError:
                call = None
            if not call or call.status != "SUCCEEDED":
                return finish("CLARIFICATION", "模型未能安全解析本轮。请提供完整标准请求，例如：给张三转2000元。",
                              previous, sources, reason=call.error_code if call else "LLM_UNAVAILABLE")
            with s.lock:
                self.store.consume(conversation_id, user_id, turn_id, revision, call)
            fields = call.output
        if fields["task_type"] == "UNKNOWN":
            return finish("CLARIFICATION", "当前意图仍不明确，请提供完整请求。")
        if fields["needs_clarification"]:
            message = "请补充转账金额，例如：2000元。" if fields["task_type"] == "TRANSFER" else "请提供账单月份，例如：2026-09。"
            return finish("CLARIFICATION", message, fields, sources)
        try:
            normalized = normalize_fields(fields)
            if edit and fields['task_type']=='TRANSFER':normalized['currency']=edit_currency
        except Rejected:
            partial = fields | {"amount_text": "", "month_text": "", "needs_clarification": True}
            return finish("CLARIFICATION", "关键字段无效或存在歧义，请使用明确数字金额或有效月份。", partial, sources)
        normalized["input_evidence"] = {key: sources[key] for key in ("recipient_text", "amount_text", "month_text") if fields.get(key) and key in sources}
        def current():
            return self.store.assert_current(conversation_id, user_id, turn_id, revision)
        task = s.create_normalized_task(text, user_id, source_account_id, normalized,
                                        conversation_id, turn_id, revision, current)
        message = "请核对授权卡，通过确认按钮批准转账。" if task["authorization"] else task.get("clarification") or "任务已处理，结果见下方。"
        return finish(task["status"], message, task_id=task["task_id"],
                      llm_request_id=call.request_id if call else None)

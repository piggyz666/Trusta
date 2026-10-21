from backend.business import month_window
from backend.domain import Rejected


class ReadOnlyScenarios:
    """Deterministic orchestration; only runtime persists Agent outputs and task state."""
    def __init__(self, service):
        self.service = service

    def _work(self, task_id, step_id, actor, key, fn):
        s = self.service
        def run():
            result = fn()
            task = s.blackboard.get(task_id)
            context = task["context"]
            context[key] = result
            s.blackboard.update(task_id, actor, context=context)
            s.audit.append(task_id, None, "READ_ONLY_STEP_RESULT", actor,
                           {"step_id": step_id, "payload_ref": f"task:{task_id}:context:{key}", "result": result})
            return result
        return s._step(task_id, step_id, run)

    def run(self, task_id, source_account_id):
        s = self.service
        task = s.blackboard.get(task_id)
        kind, user_id = task["task_type"], task["user_id"]
        actor = "finance" if kind == "BILL_ANALYSIS" else "wealth"
        first = task["steps"][0]["step_id"]
        try:
            s.scheduler.start(task_id, first)
            if task["context"]["normalized"].get("needs_clarification"):
                s.scheduler.transition(task_id, first, "WAITING_USER")
                return s.blackboard.update(task_id, "supervisor", status="WAITING_USER",
                                           clarification=task["context"]["normalized"]["clarification"])
            s.scheduler.transition(task_id, first, "COMPLETED")
            if kind == "BILL_ANALYSIS":
                normalized = task["context"]["normalized"]
                source=self._work(task_id, "F02", actor, "source_account", lambda: s.finance.account(user_id, source_account_id))
                if 'year' in normalized:
                    year = normalized['year']
                    evidence = self._work(task_id,'F03',actor,'consumption',lambda:s.finance.annual_records(user_id,source_account_id,year))
                    report = self._work(task_id,'F04',actor,'annual_analysis',lambda:s.finance.annual(year,evidence,source['currency']))
                    flags=[]
                    for month,rows in evidence.items():
                        flags.extend([{**flag,'month':month} for flag in s.finance.anomalies(rows,source['currency'])['flags']])
                    report['anomalies']=self._work(task_id,'F05',actor,'anomalies',lambda:{'flags':flags,'basis':'PER_MONTH_RULES'})
                else:
                    month = normalized['month']
                    def records():
                        return {"month": month, "current": s.finance.records(user_id, source_account_id, month),
                                "previous": s.finance.records(user_id, source_account_id, month_window(month)[2])}
                    evidence = self._work(task_id, "F03", actor, "consumption", records)
                    report = self._work(task_id, "F04", actor, "monthly_analysis",
                                        lambda: s.finance.analyze(month, evidence["current"], evidence["previous"],source['currency']))
                    report["anomalies"] = self._work(task_id, "F05", actor, "anomalies", lambda: s.finance.anomalies(evidence["current"],source['currency']))
                final_step = "F06"
            else:
                profile = self._work(task_id, "W02", actor, "risk_profile", lambda: s.wealth.risk_profile(user_id))
                products = self._work(task_id, "W03", actor, "product_catalog", lambda: s.wealth.products(user_id))
                matched = self._work(task_id, "W04", actor, "matched_products", lambda: s.wealth.match(profile, products))
                report = self._work(task_id, "W05", actor, "recommendation_report", lambda: s.wealth.explain(profile, matched))
                final_step = "W06"
            s._agent_message(actor, "supervisor", task_id, final_step, "READ_ONLY_REPORT", report)
            def publish():
                ref = s.audit.append(task_id, None, "READ_ONLY_REPORT_PUBLISHED", "runtime", report)
                s.blackboard.update(task_id, "runtime", result=report, audit_refs=[ref])
            s._step(task_id, final_step, publish)
            return s.blackboard.update(task_id, "runtime", status="COMPLETED")
        except Rejected as exc:
            current = s.blackboard.get(task_id)
            running = next((step for step in current["steps"] if step["status"] == "RUNNING"), None)
            waiting = exc.code in {"RISK_PROFILE_REQUIRED", "RISK_PROFILE_EXPIRED"}
            if running:
                s.scheduler.transition(task_id, running["step_id"], "WAITING_USER" if waiting else "BLOCKED")
            s.audit.append(task_id, None, "READ_ONLY_REPORT_REJECTED", "runtime", {"reason": exc.code})
            return s.blackboard.update(task_id, "runtime", status="WAITING_USER" if waiting else "BLOCKED", errors=[exc.code],
                                       clarification=str(exc) if waiting else None)
        except Exception:
            current = s.blackboard.get(task_id)
            running = next((step for step in current["steps"] if step["status"] == "RUNNING"), None)
            if running:
                s.scheduler.transition(task_id, running["step_id"], "FAILED")
            s.audit.append(task_id, None, "READ_ONLY_REPORT_FAILED", "runtime", {"reason": "READ_ONLY_ANALYSIS_FAILED"})
            return s.blackboard.update(task_id, "runtime", status="FAILED", errors=["READ_ONLY_ANALYSIS_FAILED"])

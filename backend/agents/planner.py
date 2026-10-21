from backend.business import business_dag


class PlannerAgent:
    def plan(self, task):
        return {"task_id": task["task_id"], "steps": business_dag(task["task_type"]), "task_type": task["task_type"]}

    def action_branch(self, task, prefix):
        from backend.domain import Rejected
        from backend.runtime.banking_plan import TASKS,action_steps
        if task["task_type"] not in TASKS:
            raise Rejected("UNSUPPORTED_TASK_TYPE")
        return action_steps(prefix,"R05" if task["task_type"]=="CROSS_SCENARIO" else "R03")

    def propose_template(self, context, infer):
        from backend.domain import uid
        from backend.llm.contracts import InferenceRequest
        return infer(InferenceRequest(uid(), context, "plan_proposal", {"task_type": context.task_type}))

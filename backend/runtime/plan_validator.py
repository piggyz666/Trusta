from backend.business import business_dag
from backend.domain import Rejected


class PlanValidator:
    """V1 safety steps and owners are fixed, including every authorization dependency."""
    def validate(self, task_id, task_type, plan):
        if (not isinstance(plan, dict) or set(plan) != {"task_id", "task_type", "steps"}
                or plan["task_id"] != task_id or plan["task_type"] != task_type
                or plan["steps"] != business_dag(task_type)):
            raise Rejected("INVALID_TASK_DAG")
        return plan

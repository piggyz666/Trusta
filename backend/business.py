"""Read-only business task contracts; the V1 transfer DAG stays unchanged."""
from datetime import datetime, timedelta, timezone
import re
from backend.domain import Rejected, transfer_dag

CHINA = timezone(timedelta(hours=8))
TASK_TYPES = {"TRANSFER", "BILL_ANALYSIS", "WEALTH_RECOMMENDATION"}
from backend.runtime.banking_plan import TASKS as BANKING_TASKS
TASK_TYPES |= BANKING_TASKS
TASK_TYPES.add("GENERAL_GRAPH")


def month_window(month):
    if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise Rejected("INVALID_MONTH")
    try:
        year, number = map(int, month.split("-"))
        if not 1900 <= year <= 9998:
            raise ValueError()
        start = datetime(year, number, 1, tzinfo=CHINA)
        end = datetime(year + (number == 12), 1 if number == 12 else number + 1, 1, tzinfo=CHINA)
        prior = (start - timedelta(days=1)).strftime("%Y-%m")
        return start.timestamp(), end.timestamp(), prior
    except ValueError:
        raise Rejected("INVALID_MONTH")


def business_dag(task_type):
    if task_type in BANKING_TASKS:
        from backend.runtime.banking_plan import initial_plan
        return initial_plan(task_type)
    if task_type == "TRANSFER":
        return transfer_dag()
    if task_type == "BILL_ANALYSIS":
        steps = [("F01", "Parse Analysis Request", "supervisor"), ("F02", "Read Account", "finance"),
                 ("F03", "Read Consumption Records", "finance"), ("F04", "Aggregate Categories and Months", "finance"),
                 ("F05", "Detect Transaction Anomalies", "finance"), ("F06", "Publish Analysis", "runtime")]
    elif task_type == "WEALTH_RECOMMENDATION":
        steps = [("W01", "Parse Recommendation Request", "supervisor"), ("W02", "Read Risk Profile", "wealth"),
                 ("W03", "Read Product Catalog", "wealth"), ("W04", "Match Suitable Products", "wealth"),
                 ("W05", "Generate Grounded Reasons", "wealth"), ("W06", "Publish Recommendations", "runtime")]
    else:
        raise Rejected("UNSUPPORTED_TASK_TYPE")
    return [{"step_id": code, "name": name, "owner": owner, "dependencies": [steps[i-1][0]] if i else [],
             "status": "PENDING"} for i, (code, name, owner) in enumerate(steps)]

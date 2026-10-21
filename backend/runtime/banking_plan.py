"""Deterministic semantic templates and per-action safety DAG branches."""
from backend.domain import Rejected

TASKS = {"CARD_MANAGEMENT", "SUBSCRIPTION_MANAGEMENT", "DIRECT_DEBIT_MANAGEMENT", "PRODUCT_COMPARE",
         "INVESTMENT_PURCHASE", "INVESTMENT_REDEEM", "CROSS_SCENARIO", "SCHEDULED_TRANSFER", "SPLIT_REQUEST",
         "WISH_FUND_MANAGEMENT", "RISK_ASSESSMENT", "PAYEE_MANAGEMENT"}
OWNERS = {"CARD_MANAGEMENT":"service", "SUBSCRIPTION_MANAGEMENT":"service", "DIRECT_DEBIT_MANAGEMENT":"service",
          "PRODUCT_COMPARE":"wealth", "INVESTMENT_PURCHASE":"wealth", "INVESTMENT_REDEEM":"wealth",
          "CROSS_SCENARIO":"finance", "SCHEDULED_TRANSFER":"transaction", "SPLIT_REQUEST":"transaction",
          "WISH_FUND_MANAGEMENT":"service", "RISK_ASSESSMENT":"wealth", "PAYEE_MANAGEMENT":"transaction"}


def initial_plan(kind):
    if kind not in TASKS:
        raise Rejected("UNSUPPORTED_TASK_TYPE")
    owner = OWNERS[kind]
    if kind == "CROSS_SCENARIO":
        rows = [("R01","Parse Goal","supervisor",[]),("R02","Analyze Spending","finance",["R01"]),
                ("R03","Read Authoritative Subscriptions","service",["R01"]),
                ("R04","Detect Periodic Candidates","finance",["R02"]),
                ("R05","Resolve Candidates to Authority","service",["R03","R04"])]
    else:
        rows = [("R01","Parse Goal","supervisor",[]),("R02","Read Authoritative Domain",owner,["R01"]),
                ("R03","Build Grounded Result or Proposal",owner,["R02"])]
    return [{"step_id":code,"name":name,"owner":actor,"dependencies":deps,"status":"PENDING"} for code,name,actor,deps in rows]


def action_steps(prefix, dependency):
    stages = [("S","Schema and Capability","schema"),("P","Policy Recheck","policy"),("A","Individual Authorization","authorization"),
              ("E","Execute Authorized Action","executor"),("V","Verify Authoritative State","verifier"),("R","Receipt","receipts")]
    return [{"step_id":prefix+suffix,"name":name,"owner":owner,"dependencies":[dependency if i==0 else prefix+stages[i-1][0]],
             "status":"PENDING"} for i,(suffix,name,owner) in enumerate(stages)]


def validate_extension(task, steps, prefix):
    dependency = "R05" if task["task_type"]=="CROSS_SCENARIO" else "R03"
    if (task["task_type"] not in TASKS or dependency not in {s["step_id"] for s in task["steps"]}
            or steps != action_steps(prefix,dependency) or any(s["step_id"].startswith(prefix) for s in task["steps"])):
        raise Rejected("INVALID_TASK_DAG")

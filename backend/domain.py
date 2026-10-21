from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import uuid


class Rejected(Exception):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)


def uid():
    return str(uuid.uuid4())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError()
        return int(amount * 100)
    except (ValueError, InvalidOperation, OverflowError):
        raise Rejected("REJECTED_SCHEMA", "金额必须为正数，且最多两位小数")


@dataclass(frozen=True)
class BankActionIR:
    action_id: str
    task_id: str
    action_type: str
    initiating_agent: str
    customer_id: str
    source_account_id: str
    recipient_id: str
    amount: int  # CNY minor units, never floating point
    currency: str
    requested_capability: str
    reason: str
    evidence_refs: list
    preconditions: dict
    expected_postconditions: dict
    created_at: float
    expires_at: float
    idempotency_key: str

    def to_dict(self):
        return asdict(self)


STEPS = ["Parse User Goal", "Resolve Recipient", "Resolve Source Account", "Check Available Balance",
         "Build BankActionIR", "Policy Evaluation", "User Authorization", "Execute Transfer",
         "Verify Result", "Generate Receipt"]
STEP_STATES = {"PENDING", "READY", "RUNNING", "WAITING_USER", "COMPLETED", "FAILED", "BLOCKED", "MANUAL_REVIEW", "CANCELLED"}
TRANSFER_STATES = {"CREATED", "AUTHORIZED", "PROCESSING", "SETTLED", "FAILED", "REVERSED"}


def transfer_dag():
    owners = ["supervisor", "transaction", "transaction", "transaction", "transaction", "policy",
              "authorization", "executor", "verifier", "receipts"]
    return [{"step_id": f"T{i:02}", "name": name, "owner": owners[i-1],
             "dependencies": [f"T{i-1:02}"] if i > 1 else [], "status": "PENDING"}
            for i, name in enumerate(STEPS, 1)]

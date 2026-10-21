"""Versioned BankActionIR contracts and deterministic sandbox domain rules."""
from dataclasses import asdict, dataclass
import copy
import math
import time
from backend.domain import Rejected, digest, uid

ACTION_SPECS = {
    "CARD_APPLY": {"agent":"service", "kind":"card_application", "capability":"banking.card.apply", "tool":"card.apply"},
    "CARD_REPORT_LOST": {"agent":"service", "kind":"card", "capability":"banking.card.report_lost", "tool":"card.report_lost"},
    "CARD_FREEZE": {"agent": "service", "kind": "card", "capability": "banking.card.freeze", "tool": "card.freeze"},
    "CARD_UNFREEZE": {"agent": "service", "kind": "card", "capability": "banking.card.unfreeze", "tool": "card.unfreeze"},
    "CARD_LIMIT_CHANGE": {"agent": "service", "kind": "card", "capability": "banking.card.limit", "tool": "card.limit_change"},
    "CARD_TRANSACTION_CONTROL": {"agent": "service", "kind": "card", "capability": "banking.card.control", "tool": "card.transaction_control"},
    "SUBSCRIPTION_CANCEL": {"agent": "service", "kind": "subscription", "capability": "banking.subscription.cancel", "tool": "subscription.cancel"},
    "DIRECT_DEBIT_DISABLE": {"agent": "service", "kind": "direct_debit", "capability": "banking.direct_debit.disable", "tool": "direct_debit.disable"},
    "INVESTMENT_PURCHASE": {"agent": "wealth", "kind": "product", "capability": "banking.investment.purchase", "tool": "investment.purchase"},
    "INVESTMENT_REDEEM": {"agent": "wealth", "kind": "product", "capability": "banking.investment.redeem", "tool": "investment.redeem"},
    # B8 additions. Same generic pipeline: snapshot -> domain rules -> expected state -> execute -> independent verify.
    "CARD_CANCEL_LOST": {"agent": "service", "kind": "card", "capability": "banking.card.cancel_lost", "tool": "card.cancel_lost"},
    "CARD_PIN_RESET": {"agent": "service", "kind": "card", "capability": "banking.card.pin_reset", "tool": "card.pin_reset"},
    "FUND_LOCK": {"agent": "service", "kind": "wish_fund", "capability": "banking.wish_fund.lock", "tool": "wish_fund.lock"},
    "FUND_RELEASE": {"agent": "service", "kind": "wish_fund", "capability": "banking.wish_fund.release", "tool": "wish_fund.release"},
    "WISH_ORDER": {"agent": "service", "kind": "wish_fund", "capability": "banking.wish_fund.order", "tool": "wish_fund.order"},
    "RISK_PROFILE_UPDATE": {"agent": "wealth", "kind": "risk_profile", "capability": "banking.risk_profile.update", "tool": "risk_profile.update"},
}
WISH_FUND_MAX_SECONDS = 400 * 86400
RISK_PROFILE_SECONDS = 365 * 86400


@dataclass(frozen=True)
class BankActionIRV2:
    schema_version: int
    action_id: str
    task_id: str
    action_type: str
    initiating_agent: str
    customer_id: str
    source_account_id: str
    target: dict
    parameters: dict
    requested_capability: str
    reason: str
    evidence_refs: list
    preconditions: dict
    expected_postconditions: dict
    created_at: float
    expires_at: float
    idempotency_key: str


def validate_parameters(kind, values):
    if not isinstance(values, dict):
        raise Rejected("REJECTED_SCHEMA")
    keys = {"CARD_LIMIT_CHANGE": {"spending_limit"}, "CARD_TRANSACTION_CONTROL":
            {"online_enabled", "international_enabled", "contactless_enabled"},
            "INVESTMENT_PURCHASE": {"amount", "currency"}, "INVESTMENT_REDEEM": {"amount", "currency"},
            "FUND_LOCK": {"amount", "currency", "name", "release_at"}, "WISH_ORDER": {"amount", "currency", "items"},
            "RISK_PROFILE_UPDATE": {"risk_level", "assessed_at", "expires_at", "answers_hash"}}
    if set(values) != keys.get(kind, set()):
        raise Rejected("REJECTED_SCHEMA")
    if kind == "FUND_LOCK" and (not isinstance(values["name"], str) or not 1 <= len(values["name"]) <= 40
                                or type(values["release_at"]) not in (int, float) or not math.isfinite(values["release_at"])):
        raise Rejected("REJECTED_SCHEMA")
    if kind == "WISH_ORDER":
        items = values["items"]
        if (not isinstance(items, list) or not 1 <= len(items) <= 5
                or any(not isinstance(i, dict) or set(i) != {"merchant_id", "amount"} or not isinstance(i["merchant_id"], str)
                       or not 1 <= len(i["merchant_id"]) <= 64 or type(i["amount"]) is not int or i["amount"] <= 0 for i in items)
                or len({i["merchant_id"] for i in items}) != len(items) or sum(i["amount"] for i in items) != values["amount"]):
            raise Rejected("REJECTED_SCHEMA")
    if kind == "RISK_PROFILE_UPDATE" and (
            type(values["risk_level"]) is not int or not 1 <= values["risk_level"] <= 5
            or any(type(values[k]) not in (int, float) or not math.isfinite(values[k]) for k in ("assessed_at", "expires_at"))
            or values["expires_at"] - values["assessed_at"] != RISK_PROFILE_SECONDS
            or not isinstance(values["answers_hash"], str) or len(values["answers_hash"]) != 64):
        raise Rejected("REJECTED_SCHEMA")
    if "spending_limit" in values and (type(values["spending_limit"]) is not int or not 0 <= values["spending_limit"] <= 10**9):
        raise Rejected("REJECTED_SCHEMA")
    if kind == "CARD_TRANSACTION_CONTROL" and any(type(value) is not bool for value in values.values()):
        raise Rejected("REJECTED_SCHEMA")
    if "amount" in values and (type(values["amount"]) is not int or not 0 < values["amount"] <= 10**12 or values["currency"] != "CNY"):
        raise Rejected("REJECTED_SCHEMA")


def domain_errors(kind, snapshot, parameters, now=None):
    """Same authoritative rules are applied again inside the bank transaction."""
    now = time.time() if now is None else now
    account, target = snapshot["account"], snapshot["target"]
    errors = []
    if account["frozen"]:
        errors.append("FROZEN_SOURCE")
    if account["currency"] != "CNY":
        errors.append("UNSUPPORTED_CURRENCY")
    if not target:
        return errors + ["TARGET_NOT_FOUND"]
    if kind == 'CARD_APPLY':
        if target['status']!='AVAILABLE': errors.append('CARD_APPLICATION_ALREADY_SUBMITTED')
    elif kind == "FUND_LOCK":
        if target["status"] != "AVAILABLE":
            errors.append("WISH_FUND_EXISTS")
        if parameters["amount"] > account["balance"]:
            errors.append("INSUFFICIENT_BALANCE")
        if not now < parameters["release_at"] <= now + WISH_FUND_MAX_SECONDS:
            errors.append("INVALID_RELEASE_TIME")
    elif kind == "FUND_RELEASE":
        if target["status"] != "ACTIVE":
            errors.append("WISH_FUND_NOT_ACTIVE")
    elif kind == "WISH_ORDER":
        if target["status"] != "ACTIVE":
            errors.append("WISH_FUND_NOT_ACTIVE")
        catalogue = {m["id"]: m for m in snapshot["merchants"]}
        for item in parameters["items"]:
            merchant = catalogue.get(item["merchant_id"])
            if not merchant or merchant["status"] != "ACTIVE":
                errors.append("MERCHANT_UNAVAILABLE")
            elif merchant["price"] != item["amount"]:
                errors.append("MERCHANT_PRICE_MISMATCH")  # prices come from the bank's merchant catalogue, never the model
        if parameters["amount"] > target["balance"]:
            errors.append("WISH_FUND_INSUFFICIENT")
    elif kind == "RISK_PROFILE_UPDATE":
        if not parameters["assessed_at"] <= now < parameters["expires_at"]:
            errors.append("RISK_ASSESSMENT_EXPIRED")
    elif kind.startswith("CARD_"):
        allowed = ({'ACTIVE','FROZEN'} if kind in {'CARD_REPORT_LOST','CARD_PIN_RESET'} else {'FROZEN'} if kind=='CARD_UNFREEZE'
                   else {'LOST'} if kind=='CARD_CANCEL_LOST' else {'ACTIVE'})
        if target['status'] not in allowed:
            errors.append("CARD_STATE_INVALID")
    elif kind == "SUBSCRIPTION_CANCEL":
        if target["status"] != "ACTIVE" or not target["cancelable"]:
            errors.append("SUBSCRIPTION_NOT_CANCELABLE")
    elif kind == "DIRECT_DEBIT_DISABLE":
        if target["status"] != "ACTIVE":
            errors.append("DIRECT_DEBIT_STATE_INVALID")
        if not target["cancelable"]:
            errors.append("DIRECT_DEBIT_NOT_DISABLEABLE")
    else:
        amount, portfolio = parameters["amount"], snapshot["portfolio"]
        if target["currency"] != parameters["currency"]:
            errors.append("PRODUCT_CURRENCY_MISMATCH")
        restriction = snapshot["restrictions"]
        if kind == "INVESTMENT_PURCHASE":
            profile = snapshot["risk_profile"]
            if not profile or not profile["assessed_at"] <= now < profile["expires_at"]:
                errors.append("RISK_PROFILE_REQUIRED")
            elif not 1 <= profile["risk_level"] <= 5 or target["risk_level"] > profile["risk_level"]:
                errors.append("SUITABILITY_RISK_MISMATCH")
            if not target["active"]:
                errors.append("PRODUCT_INACTIVE")
            if amount < target["minimum_amount"]:
                errors.append("BELOW_PRODUCT_MINIMUM")
            if amount > account["balance"]:
                errors.append("INSUFFICIENT_BALANCE")
            if not restriction["purchase_enabled"]:
                errors.append("PRODUCT_PURCHASE_RESTRICTED")
        else:
            if amount > portfolio["amount_cents"]:
                errors.append("INSUFFICIENT_HOLDING")
            if portfolio["redeemable_at"] > now:
                errors.append("HOLDING_PERIOD_NOT_MET")
            if not restriction["redemption_enabled"]:
                errors.append("PRODUCT_REDEMPTION_RESTRICTED")
    return errors


def expected_state(kind, snapshot, values, approval_deadline):
    # Conditions used for financial approval; hard verification independently reads the bank.
    result = copy.deepcopy(snapshot)
    target, account = result["target"], result["account"]
    if not target:
        return result
    if kind=='CARD_APPLY':
        target['status']='SUBMITTED'
    elif kind=='CARD_REPORT_LOST':
        target.update(status='LOST',online_enabled=0,international_enabled=0,contactless_enabled=0)
    elif kind == 'CARD_CANCEL_LOST':
        target['status'] = 'ACTIVE'  # controls stay off after loss; the customer re-enables them explicitly
    elif kind == 'CARD_PIN_RESET':
        pass  # only the version changes; the new PIN is set out of band and never passes through the agent
    elif kind == "FUND_LOCK":
        account["balance"] -= values["amount"]
        account["version"] += 1
        target.update(name=values["name"], balance=values["amount"], target_amount=values["amount"],
                      release_at=values["release_at"], status="ACTIVE")
    elif kind == "FUND_RELEASE":
        account["balance"] += target["balance"]
        account["version"] += 1
        target.update(balance=0, status="RELEASED")
    elif kind == "WISH_ORDER":
        target["balance"] -= values["amount"]
    elif kind == "RISK_PROFILE_UPDATE":
        target.update(risk_level=values["risk_level"], assessed_at=values["assessed_at"], expires_at=values["expires_at"])
        return result
    elif kind in {"CARD_FREEZE", "CARD_UNFREEZE"}:
        target["status"] = "FROZEN" if kind == "CARD_FREEZE" else "ACTIVE"
    elif kind == "CARD_LIMIT_CHANGE":
        target["spending_limit"] = values["spending_limit"]
    elif kind == "CARD_TRANSACTION_CONTROL":
        target.update({key: int(value) for key, value in values.items()})
    elif kind == "SUBSCRIPTION_CANCEL":
        target["status"] = "CANCELLED"
    elif kind == "DIRECT_DEBIT_DISABLE":
        target["status"] = "DISABLED"
    else:
        sign = 1 if kind == "INVESTMENT_PURCHASE" else -1
        account["balance"] -= sign * values["amount"]
        account["version"] += 1
        portfolio = result["portfolio"]
        portfolio["amount_cents"] += sign * values["amount"]
        portfolio["version"] += 1
        if sign == 1 and target["term_days"]>0:
            # Approval deadline is >= actual execution time. This cannot shorten the stated holding period.
            portfolio["redeemable_at"] = max(portfolio["redeemable_at"], approval_deadline + target["term_days"] * 86400)
        return result
    target["version"] += 1
    return result


def risk_target(profile, customer_id):
    """Risk profile as an action target: same shape whether or not a profile exists yet."""
    base = {"customer_id": customer_id, "risk_level": 0, "assessed_at": 0.0, "expires_at": 0.0}
    return {"id": customer_id} | (dict(profile) if profile else base)


def snapshot_from_gateway(gateway, actor, user_id, account_id, kind, target_id):
    account = gateway.read(actor, user_id, "account.get", account_id=account_id)
    resource_kind = ACTION_SPECS[kind]["kind"]
    if resource_kind == "wish_fund":
        target = gateway.read(actor, user_id, "wish_fund.get", resource_id=target_id)
        if target["account_id"] != account_id:
            raise Rejected("RESOURCE_ACCOUNT_MISMATCH")
        return {"account": account, "target": target, "merchants": gateway.read(actor, user_id, "merchant.list")}
    if resource_kind == "risk_profile":
        if target_id != user_id:
            raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
        return {"account": account, "target": risk_target(gateway.read(actor, user_id, "customer.get_risk_profile"), user_id)}
    if resource_kind != "product":
        target = gateway.read(actor, user_id, resource_kind + ".get", resource_id=target_id)
        if target and target["account_id"] != account_id:
            raise Rejected("RESOURCE_ACCOUNT_MISMATCH")
        return {"account": account, "target": target}
    products = gateway.read(actor, user_id, "wealth.list_products")
    entry = next((row for row in products if row["id"] == target_id), None)
    position = gateway.read(actor, user_id, "portfolio.get", account_id=account_id, product_id=target_id)
    return {"account": account, "target": entry, "portfolio": position,
            "risk_profile": gateway.read(actor, user_id, "customer.get_risk_profile"),
            "restrictions": entry.pop("restrictions") if entry else {"purchase_enabled": 0, "redemption_enabled": 0}}


def build_action(task, kind, target_id, parameters, snapshot, ttl):
    validate_parameters(kind, parameters)
    now, spec = time.time(), ACTION_SPECS[kind]
    return asdict(BankActionIRV2(2, uid(), task["task_id"], kind, spec["agent"], task["user_id"],
                    snapshot["account"]["id"], {"kind": spec["kind"], "id": target_id}, parameters,
                    spec["capability"], task["user_goal"], ["bank:"+spec["kind"]+":"+target_id], snapshot,
                    expected_state(kind, snapshot, parameters, now+ttl), now, now+ttl, uid()))


def validate_action(action, gateway):
    def require(condition):
        if not condition:
            raise Rejected("REJECTED_SCHEMA")
    try:
        BankActionIRV2(**action)
        spec = ACTION_SPECS[action["action_type"]]
        require(type(action["schema_version"]) is int and action["schema_version"] == 2)
        require(action["initiating_agent"] == spec["agent"] and action["requested_capability"] == spec["capability"])
        require(set(action["target"]) == {"kind", "id"} and action["target"]["kind"] == spec["kind"])
        for field in ("action_id", "task_id", "customer_id", "source_account_id", "reason", "idempotency_key"):
            require(isinstance(action[field], str) and 0 < len(action[field]) <= 1000)
        require(isinstance(action["target"]["id"], str) and 0 < len(action["target"]["id"]) <= 100)
        require(isinstance(action["evidence_refs"], list) and action["evidence_refs"])
        require(all(isinstance(ref, str) and ref for ref in action["evidence_refs"]))
        require(all(type(action[key]) in (float, int) and math.isfinite(action[key]) for key in ("created_at", "expires_at")))
        require(action["created_at"] <= time.time() < action["expires_at"])
        validate_parameters(action["action_type"], action["parameters"])
        account = gateway.read("schema", action["customer_id"], "account.get", account_id=action["source_account_id"])
        require(account["currency"] == "CNY")
        pre = action["preconditions"]
        require(pre["account"]["id"] == account["id"] and pre["account"]["customer_id"] == action["customer_id"])
        require(digest(action["expected_postconditions"]) == digest(expected_state(action["action_type"], pre, action["parameters"], action["expires_at"])))
        # Strict snapshot shape comes from fresh, customer-scoped authoritative reads.
        current = snapshot_from_gateway(gateway, "schema", action["customer_id"], account["id"], action["action_type"], action["target"]["id"])
        require(set(pre) == set(current) and pre["target"] and pre["target"]["id"] == action["target"]["id"])
        for name in current:
            if isinstance(current[name], list):  # catalogue snapshots (e.g. merchants) are lists of rows
                require(isinstance(pre[name], list) and all(isinstance(row, dict) for row in pre[name]))
                continue
            require((current[name] is None and pre[name] is None) or isinstance(pre[name], dict) and set(pre[name]) == set(current[name]))
            if current[name] is not None:
                require(all(type(pre[name][key]) is type(value) for key,value in current[name].items()))
    except (AssertionError, KeyError, TypeError, ValueError, Rejected):
        raise Rejected("REJECTED_SCHEMA") from None

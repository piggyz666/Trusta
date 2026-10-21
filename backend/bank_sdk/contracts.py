from dataclasses import dataclass, field
from typing import Protocol
from backend.domain import Rejected

STATUSES = frozenset({'CREATED','PENDING','AUTHORIZED','PROCESSING','SETTLED','FAILED','REVERSED','UNKNOWN'})
# Aliases preserve historical signed registry commitments; aliases do not grant privileges.
CANONICAL = {
    'account.get':'accounts.read','account.get_balance':'balance.read','payee.resolve':'payees.read',
    'transaction.get':'payments.status','transaction.list':'transactions.read','transfer.create':'payments.create',
    'customer.get_risk_profile':'customers.risk.read','wealth.list_products':'investments.products.read',
    'card.list':'cards.read','card.get':'cards.read','card.freeze':'cards.freeze','card.unfreeze':'cards.unfreeze',
    'card_application.get':'cards.applications.read','card.apply':'cards.applications.create','card.report_lost':'cards.report_lost',
    'card.limit_change':'cards.limit.change','card.transaction_control':'cards.controls.update',
    'subscription.list':'subscriptions.read','subscription.get':'subscriptions.read','subscription.cancel':'subscriptions.cancel',
    'direct_debit.list':'direct_debits.read','direct_debit.get':'direct_debits.read','direct_debit.disable':'direct_debits.disable',
    'portfolio.get':'investments.portfolio.read','investment.purchase':'investments.purchase','investment.redeem':'investments.redeem',
    'operation.get':'operations.read','operation.evidence':'operations.evidence.read','transaction.recurring_history':'transactions.recurring.read',
    'card.cancel_lost':'cards.report_lost.cancel','card.pin_reset':'cards.pin.reset',
    'wish_fund.get':'savings.goals.read','wish_fund.list':'savings.goals.read','wish_fund.lock':'savings.goals.lock',
    'wish_fund.release':'savings.goals.release','wish_fund.order':'savings.goals.pay','merchant.list':'merchants.read',
    'life_event.list':'customers.events.read','risk_profile.update':'customers.risk.update'}

class BankError(Rejected):
    def __init__(self, code, http_status=None, retry_after=None):
        self.http_status, self.retry_after = http_status, retry_after
        super().__init__(code)

class UncertainBankWrite(Exception):
    code = 'EXTERNAL_WRITE_UNKNOWN'

@dataclass(frozen=True)
class BankCapabilityManifest:
    provider: str
    environment: str
    capabilities: frozenset[str]
    currencies: frozenset[str]
    asynchronous: bool = False
    rate_limit_per_second: int | None = None
    rate_limit_per_day: int | None = None

@dataclass(frozen=True)
class BankIdempotency:
    key: str
    action_hash: str

@dataclass(frozen=True)
class BankRequest:
    capability: str
    customer_id: str
    parameters: dict = field(default_factory=dict)
    action: dict | None = None
    idempotency: BankIdempotency | None = None
    deadline: float | None = None

@dataclass(frozen=True)
class BankResponse:
    data: object
    provider: str
    environment: str
    status: str | None = None

@dataclass(frozen=True)
class BankWebhookEvent:
    event_id: str
    resource_id: str
    event_type: str
    payload_hash: str
    authority: str = 'QUERY_TRIGGER_ONLY'

@dataclass(frozen=True)
class BankVerificationResult:
    status: str
    reason: str
    provider_status: str
    evidence_hash: str
    executed_at: float | None = None

class BankAuthProvider(Protocol):
    def headers(self, method: str, path: str, body: bytes) -> dict: ...
    def revoke(self) -> None: ...

class BankProvider(Protocol):
    manifest: BankCapabilityManifest
    def request(self, request: BankRequest) -> BankResponse: ...
    def verify(self, action: dict, transaction_id: str) -> BankVerificationResult: ...

@dataclass(frozen=True)
class ExternalBankSafeMode:
    environment: str = 'sandbox'
    enabled: bool = False
    source_accounts: frozenset[str] = frozenset()
    payees: frozenset[str] = frozenset()
    maximum_amount: int = 100
    scheduled_enabled: bool = False

    def check(self, action, scheduled=False):
        if (self.environment not in {'sandbox','production'} or not self.enabled
                or type(self.maximum_amount) is not int or self.maximum_amount <= 0
                or action['source_account_id'] not in self.source_accounts or action['recipient_id'] not in self.payees
                or type(action['amount']) is not int or not 0 < action['amount'] <= self.maximum_amount
                or scheduled and not self.scheduled_enabled):
            raise BankError('EXTERNAL_SAFE_MODE_DENIED')

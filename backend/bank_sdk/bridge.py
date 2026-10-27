"""Historical ToolGateway protocol -> canonical SDK. No vendor endpoints here."""
from backend.bank_sdk.contracts import CANONICAL, BankRequest, BankIdempotency
from backend.domain import Rejected

class CanonicalBankAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.external = provider.manifest.environment != 'local'
        self.supported_currencies = provider.manifest.currencies
        self.manifest = {tool:{'risk_class':'WRITE' if (tool.startswith('finance.') and tool!='finance.get') or tool in {'retail.pay','retail.refund','transfer.create','card.apply','card.report_lost','card.freeze','card.unfreeze','card.limit_change','card.transaction_control','subscription.cancel','direct_debit.disable','investment.purchase','investment.redeem',
                               'card.cancel_lost','card.pin_reset','wish_fund.lock','wish_fund.release','wish_fund.order','risk_profile.update','payee.add'} else 'READ',
                               'requires_auth':True,'supports_idempotency':True}
                         for tool,cap in CANONICAL.items() if cap in provider.manifest.capabilities}

    @property
    def bank(self):
        return self.provider.adapter.bank

    def _call(self, customer, tool, **params):
        return self.provider.request(BankRequest(CANONICAL[tool],customer,params|{'_tool':tool})).data

    def get_account(self, account_id):
        return self._call(getattr(self.provider,'customer_id',''), 'account.get',account_id=account_id)

    def get_balance(self, account_id):
        return self._call(getattr(self.provider,'customer_id',''), 'account.get_balance',account_id=account_id)

    def resolve_payee(self, customer_id, name=None, recipient_id=None):
        return self._call(customer_id,'payee.resolve',name=name,recipient_id=recipient_id)

    def get_transaction(self, transaction_id):
        return self._call(getattr(self.provider,'customer_id',''),'transaction.get',transaction_id=transaction_id)

    def list_consumption(self, customer_id, account_id, month):
        return self._call(customer_id,'transaction.list',account_id=account_id,month=month)

    def get_risk_profile(self, customer_id): return self._call(customer_id,'customer.get_risk_profile')
    def list_products(self): return self._call(getattr(self.provider,'customer_id',''),'wealth.list_products')
    def read_domain(self, customer_id, capability, **params): return self._call(customer_id,capability,**params)

    def create_transfer(self, action, action_hash, deadline):
        return self.provider.request(BankRequest('payments.create',action['customer_id'],{'_tool':'transfer.create'},action,
                    BankIdempotency(action['idempotency_key'],action_hash),deadline)).data

    def apply_action(self, action, action_hash, deadline):
        from backend.banking.actions import ACTION_SPECS
        tool=ACTION_SPECS[action['action_type']]['tool']
        return self.provider.request(BankRequest(CANONICAL[tool],action['customer_id'],{'_tool':tool},action,
                    BankIdempotency(action['idempotency_key'],action_hash),deadline)).data

    def verify_external(self, action, transaction_id):
        return self.provider.verify(action,transaction_id)

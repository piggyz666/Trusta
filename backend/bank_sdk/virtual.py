from backend.bank_sdk.contracts import BankCapabilityManifest, BankResponse, CANONICAL, BankError, BankVerificationResult
from backend.domain import digest

class VirtualBankProvider:
    def __init__(self, adapter):
        self.adapter = adapter
        self.manifest = BankCapabilityManifest('virtual','local',frozenset(CANONICAL[k] for k in adapter.manifest),frozenset({'CNY'}))

    def request(self, req):
        tool = req.parameters.get('_tool')
        params = {k:v for k,v in req.parameters.items() if k != '_tool'}
        if tool not in self.adapter.manifest or CANONICAL.get(tool) != req.capability:
            raise BankError('BANK_CAPABILITY_UNAVAILABLE')
        if req.action is not None:
            method = self.adapter.create_transfer if tool == 'transfer.create' else self.adapter.apply_action
            result = method(req.action, req.idempotency.action_hash, req.deadline)
        elif tool == 'account.get': result = self.adapter.get_account(params['account_id'])
        elif tool == 'account.get_balance': result = self.adapter.get_balance(params['account_id'])
        elif tool == 'payee.resolve': result = self.adapter.resolve_payee(req.customer_id,**params)
        elif tool == 'transaction.get': result = self.adapter.get_transaction(params['transaction_id'])
        elif tool == 'transaction.list': result = self.adapter.list_consumption(req.customer_id,params['account_id'],params['month'])
        elif tool == 'customer.get_risk_profile': result = self.adapter.get_risk_profile(req.customer_id)
        elif tool == 'wealth.list_products': result = self.adapter.list_products()
        else: result = self.adapter.read_domain(req.customer_id,tool,**params)
        return BankResponse(result,'virtual','local',result.get('status') if isinstance(result,dict) else None)

    def verify(self,action,transaction_id):
        # Core still applies the stronger independent ledger/balance HardVerifier.
        row=self.adapter.get_transaction(transaction_id) if action.get('action_type')=='TRANSFER' else self.adapter.read_domain(action['customer_id'],'operation.get',operation_id=transaction_id)
        prior=self.adapter.bank.db.one('SELECT * FROM processed_idempotency_keys WHERE key=?',(action['idempotency_key'],)) if action.get('action_type')=='TRANSFER' else None
        match=bool(row and row.get('status')=='SETTLED' and ((prior and prior['action_hash']==digest(action) and prior['transaction_id']==transaction_id and row['source_account_id']==action['source_account_id'] and row['recipient_id']==action['recipient_id'] and type(row['amount']) is int and row['amount']==action['amount'] and row['currency']==action['currency']) or row.get('action_hash')==digest(action)))
        return BankVerificationResult('VERIFIED' if match else 'MANUAL_REVIEW','LOCAL_RECORD_MATCH' if match else 'LOCAL_RECORD_MISMATCH',row.get('status','UNKNOWN') if row else 'UNKNOWN',digest(row),row.get('executed_at') if match else None)

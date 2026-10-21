class AccountService:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def query_balance(self, execution_id, user_id):
        # The installed plan, not additional caller parameters, controls the read.
        record = self.sandbox.get(execution_id, user_id)
        if not any(n['action'] in {'account.get_balance', 'balances.read'} for n in record['plan']['steps']):
            from backend.llm.contracts import InferenceError
            raise InferenceError('LLM_SANDBOX_OPERATION')
        return self.sandbox.start(execution_id, user_id)

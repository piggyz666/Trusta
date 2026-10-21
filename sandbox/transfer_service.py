class TransferService:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def create_transfer_plan(self, execution_id, user_id):
        record = self.sandbox.get(execution_id, user_id)
        if not any(n['action'] in {'create_transfer_plan', 'transfer.propose'} for n in record['plan']['steps']):
            from backend.llm.contracts import InferenceError
            raise InferenceError('LLM_SANDBOX_OPERATION')
        return self.sandbox.start(execution_id, user_id)

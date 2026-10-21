class PaymentService:
    """Operator-facing exact native action approval; never passed to a Provider."""
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def create_payment(self, execution_id, user_id, node_id, action_hash, *, approve):
        return self.sandbox.authorize(execution_id, user_id, node_id, action_hash, approve)

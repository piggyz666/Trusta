class SupervisorAgent:
    def __init__(self, blackboard):
        self.blackboard = blackboard

    def receive(self, user_id, goal, normalized):
        # Receives structured Intent Normalization output, never a bank write authority.
        return self.blackboard.create(user_id, goal, normalized)

    def propose_intent(self, context, payload, infer):
        from backend.domain import uid
        from backend.llm.contracts import InferenceRequest
        return infer(InferenceRequest(uid(), context, "conversation_intent", payload))

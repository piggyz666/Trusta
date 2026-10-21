from backend.tx.state_machine import TransactionStateMachine
from backend.anchor.worker import AnchorWorker

class TransactionQueries:
    def __init__(self,db):
        self.machine=TransactionStateMachine(db)
        self.anchors=AnchorWorker(self.machine,None,None,None)
    def transaction(self,value,owner):return self.machine.by_hash(value,owner)
    def anchor(self,root,owner):return self.anchors.by_root(root,owner)

"""Local explicit-slot continuation; creates a fresh proposal, never inherits consent."""
from copy import deepcopy
import hashlib
import hmac
from backend.domain import Rejected, canonical, digest
from backend.llm.graph_protocol import input_names


class ProposalDialogue:
    def __init__(self,service): self.s=service

    def _seal(self,body):
        key=hmac.new(self.s.config.signing_key,b'TRUSTA-PROPOSAL-SLOTS-V1',hashlib.sha256).digest()
        return hmac.new(key,canonical(body).encode(),hashlib.sha256).hexdigest()

    def _remember(self,task,inputs,parent=None):
        if task['errors']!=['GRAPH_CLARIFICATION_REQUIRED'] or task.get('result',{}).get('reason')!='MISSING_INPUT': return task
        with self.s.lock:
            current=self.s._owned_task(task['task_id'],task['user_id'])
            if current['revision']!=task['revision']: raise Rejected('LLM_STALE_TASK')
            body={'user_id':task['user_id'],'task_id':task['task_id'],'goal':task['user_goal'],
                  'inputs':deepcopy(inputs),'missing':task['result']['missing_parameters'],'consumed':False,'parent_task_id':parent}
            context=deepcopy(current['context']);context['proposal_dialogue']={'body':body,'seal':self._seal(body)}
            return self.s.blackboard.update(task['task_id'],'runtime',context=context)

    def start(self,user_id,goal,inputs):
        input_names(inputs);inputs=deepcopy(inputs)
        return self._remember(self.s.graphs.propose(user_id,goal,inputs),inputs)

    def resume(self,user_id,task_id,expected_revision,patch):
        with self.s.graphs.claims.hold(task_id):
            return self._resume(user_id,task_id,expected_revision,patch)

    def _resume(self,user_id,task_id,expected_revision,patch):
        input_names(patch)
        if type(expected_revision) is not int or not patch: raise Rejected('GRAPH_CLARIFICATION_PATCH')
        with self.s.lock:
            task=self.s._owned_task(task_id,user_id)
            if task['revision']!=expected_revision: raise Rejected('LLM_STALE_TASK')
            stored=task.get('context',{}).get('proposal_dialogue',{});body=stored.get('body')
            if (task['task_type']!='GENERAL_GRAPH' or task['status']!='BLOCKED' or task['steps'] or task['proposed_actions']
                    or task['errors']!=['GRAPH_CLARIFICATION_REQUIRED'] or type(body) is not dict
                    or body.get('user_id')!=user_id or body.get('task_id')!=task_id or body.get('consumed')
                    or not isinstance(stored.get('seal'),str) or not hmac.compare_digest(stored['seal'],self._seal(body))):
                raise Rejected('GRAPH_CLARIFICATION_DENIED')
            if not set(patch)<={'input:'+key for key in body['missing']}: raise Rejected('GRAPH_CLARIFICATION_PATCH')
            inputs=deepcopy(body['inputs'])|deepcopy(patch)
            updated=deepcopy(body);updated['consumed']=True
            context=deepcopy(task['context']);context['proposal_dialogue']={'body':updated,'seal':self._seal(updated)}
            self.s.blackboard.update(task_id,'runtime',context=context)
            self.s.audit.append(task_id,None,'GRAPH_SLOTS_CONTINUED','supervisor',{'patch_keys':sorted(patch),'input_hash':digest(inputs),'authority':'NONE'})
        # No original authorizations, actions or receipts enter the new task.
        result=self.s.graphs.propose(user_id,body['goal'],inputs)
        return self._remember(result,inputs,parent=task_id)

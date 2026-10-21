"""Scoped semantic proposal through the existing Gateway and Router; no tools."""
from dataclasses import replace
import time
from backend.domain import Rejected,canonical,digest,uid
from backend.llm.contracts import InferenceRequest,InferenceContext,ProviderRequest,InferenceError,InferenceResult
from backend.llm.validation import strict_json
from backend.llm.graph_protocol import messages as proposal_messages, output_schema, decode, input_names

# Proposal repair has no execution authority. Values/permissions/grounding and
# financial errors are not repairable. The original validator runs each time.
REPAIRABLE={'GRAPH_COMPACT_SCHEMA','GRAPH_COMPACT_CALL','GRAPH_COMPACT_PARAMETERS',
    'GRAPH_COMPACT_REFERENCE','GRAPH_RESPONSE_SCHEMA','NODE_ID','OUTPUT_PROVENANCE',
    'PROVENANCE_OR_DEPENDENCY','CYCLIC_OR_MISSING_DEPENDENCY','DEPENDENCIES',
    'AUTHORITATIVE_FACT_PROVENANCE','INPUT_REFS','PARAMETER_SCHEMA','GRAPH_CLARIFICATION_SCHEMA'}


def safe_reason(exc):
    reason=str(exc)
    return reason if reason.isascii() and len(reason)<=64 and all(c.isupper() or c.isdigit() or c=='_' for c in reason) else exc.code


def repair_allowed(candidate,reason):
    if reason not in REPAIRABLE or candidate is None: return False
    text=canonical(candidate).lower()
    if any(v in text for v in ('authorization','contract','execute','__import__','eval(',
        'exec(','http://','https://','sqlite','token','private_key','signature','capability')): return False
    if isinstance(candidate,dict) and isinstance(candidate.get('calls'),list):
        for call in candidate['calls']:
            if isinstance(call,dict) and isinstance(call.get('parameters'),dict):
                if any(type(v) is not str or ':' not in v for v in call['parameters'].values()): return False
    return True

def propose(gateway,registry,task,goal,inputs,*,graph_format=None):
    if gateway.settings.mode=='disabled': return InferenceResult(uid(),'DISABLED',error_code='LLM_DISABLED')
    # Fixed endpoint privilege: existing planner identity may only propose this
    # closed semantic DSL. It conveys no bank grant and cannot change the registry.
    registry.agents.require_active('planner')
    if gateway.identity_guard: gateway.identity_guard('planner')
    if task['task_type']!='GENERAL_GRAPH' or task['steps'] or task['status']!='PENDING': raise Rejected('GRAPH_PROPOSAL_DENIED')
    if not isinstance(goal,str) or len(goal)>8000: raise Rejected('GRAPH_INVALID_GOAL')
    input_names(inputs)
    # Input values stay local. Only reference names and the explicitly supplied
    # goal enter inference; classify all actual customer goals as restricted.
    request=InferenceRequest(uid(),InferenceContext(task['task_id'],task['user_id'],'planner','GENERAL_GRAPH',task['revision']),'plan_proposal',{},sensitivity='restricted',complexity='complex')
    selected_format=graph_format or gateway.settings.graph_format
    if selected_format not in {'full_v1','compact_v2','semantic_v3'}:raise Rejected('GRAPH_PROPOSAL_FORMAT')
    compact=selected_format=='compact_v2'
    from backend.llm import compact_graph
    messages=(compact_graph.messages if compact else proposal_messages)(registry,goal,inputs)
    provider_request=ProviderRequest(messages,(compact_graph.output_schema if compact else output_schema)(registry),gateway.settings.max_output_tokens,gateway.settings.timeout_seconds)
    if selected_format=='semantic_v3':
        from backend.llm import semantic_adapter
        messages,schema=semantic_adapter.contract(registry,task['task_id'],goal,inputs)
        provider_request=replace(provider_request,messages=messages,output_schema=schema)
    if len(canonical(messages))>gateway.settings.max_request_chars: raise InferenceError('LLM_INPUT_TOO_LARGE')
    route=gateway.router.select(request,provider_request);record=gateway._claim(request)
    record.update(rejected_routes=route.rejected,estimated_costs_usd=route.estimated_costs,graph_format=selected_format)
    output=selected=None;status='FAILED';error='LLM_NO_ELIGIBLE_PROVIDER';deadline=time.monotonic()+gateway.settings.timeout_seconds
    repaired=False
    for config in route.providers[:gateway.settings.max_fallbacks+1]:
        fallback=False;current_request=provider_request
        for repair_index in range(gateway.settings.graph_repair_attempts+1):
            attempt={'provider':config.name,'configured_model':config.model,'repair_index':repair_index};record['attempts'].append(attempt)
            candidate=None
            try:
                remaining=deadline-time.monotonic()
                if remaining<=0: raise InferenceError('LLM_TIMEOUT',True)
                response=gateway.providers[config.name].infer(replace(current_request,timeout_seconds=remaining))
                # A rejected but billed response must retain usage, not vanish.
                attempt.update(usage={k:v for k,v in (response.usage or {}).items() if k in {'input_tokens','output_tokens'} and type(v) is int and 0<=v<=10**9},
                    returned_model=response.model if type(response.model) is str and len(response.model)<=128 else None)
                if time.monotonic()>=deadline: raise InferenceError('LLM_TIMEOUT',True)
                if len(response.text.encode())>65536: raise InferenceError('LLM_INVALID_OUTPUT')
                attempt['raw_output_hash']=digest(response.text)
                candidate=strict_json(response.text)
                if selected_format=='semantic_v3':
                    status,output=semantic_adapter.normalize(candidate,registry,inputs,task['task_id'],goal)
                else:
                    status,output=(compact_graph.normalize if compact else decode)(candidate,registry,inputs)
                selected=config.name;error=None;attempt.update(status=status)
                record['output_hash']=digest(output);break
            except InferenceError as exc:
                error=exc.code if exc.code in {'LLM_TIMEOUT','LLM_PROVIDER_UNAVAILABLE','LLM_INVALID_OUTPUT','LLM_PROVIDER_HTTP_ERROR','LLM_PROVIDER_CREDENTIALS','LLM_INCOMPLETE_OUTPUT','LLM_BUDGET_EXHAUSTED','LLM_REFUSED','LLM_TOOL_CALL_FORBIDDEN','LLM_PROVIDER_PROTOCOL','LLM_RESPONSE_TOO_LARGE'} else 'LLM_PROVIDER_ERROR'
                attempt.update(status='FAILED',error_code=error)
                if (exc.retryable and error=='LLM_PROVIDER_UNAVAILABLE' and not repaired
                        and repair_index<gateway.settings.graph_repair_attempts and time.monotonic()<deadline):
                    repaired=True;continue
                fallback=exc.retryable and error in {'LLM_TIMEOUT','LLM_PROVIDER_UNAVAILABLE'};break
            except Rejected as exc:
                error='REJECT_PLAN';reason=safe_reason(exc)
                attempt.update(status='FAILED',error_code=error,validation_reason=reason)
                if (not repaired and repair_index<gateway.settings.graph_repair_attempts and
                        compact and repair_allowed(candidate,reason)):
                    # Never echo candidate text or local input values; only a
                    # bounded validator code is returned as untrusted feedback.
                    feedback={'role':'user','content':canonical({'proposal_feedback':reason,
                        'instruction':'Regenerate the whole proposal using the original goal/catalog/input refs. Fix structure or dependencies only. Never guess values, privileges or extra operations.'})}
                    current_request=replace(provider_request,messages=provider_request.messages+(feedback,))
                    if len(canonical(current_request.messages))>gateway.settings.max_request_chars: break
                    eligible=gateway.router.select(request,current_request)
                    if config.name not in {p.name for p in eligible.providers}: break
                    repaired=True;continue
                break
            except Exception:
                error='LLM_PROVIDER_ERROR';attempt.update(status='FAILED',error_code=error);break
        if output or not fallback: break
    record.update(status=status if output else 'FAILED',error_code=error,provider=selected);gateway._finish(record)
    return InferenceResult(request.request_id,record['status'],output,selected,error)

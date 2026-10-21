import os
from backend.llm.providers.http_adapter import HTTPAdapter
from backend.llm.contracts import InferenceError, ProviderResponse

class GeminiProvider(HTTPAdapter):
    """Native generateContent mapping, keeping model/endpoint deployment-configurable."""
    def auth_headers(self):
        key=os.getenv(self.config.api_key_env,'')
        if not key or any(c in key for c in '\r\n'): raise InferenceError('LLM_PROVIDER_CREDENTIALS')
        return {'Content-Type':'application/json','Accept':'application/json','x-goog-api-key':key}

    @staticmethod
    def transport_schema(value):
        # Nonzero bounded arrays in the nested graph schema were rejected by the
        # tested API. Retain closed objects, types, enums and reference patterns;
        # Gateway/TaskGraphValidator still enforce all original bounds/patterns.
        # This is an explicit adapter mapping, not a retry or validation fallback.
        if isinstance(value,dict):
            return {key:GeminiProvider.transport_schema(item) for key,item in value.items()
                    if key not in {'minItems','maxItems'} or key=='maxItems' and item==0}
        if isinstance(value,list):return [GeminiProvider.transport_schema(item) for item in value]
        return value

    def body(self, request):
        system='\n'.join(m['content'] for m in request.messages if m['role']=='system')
        body={'systemInstruction':{'parts':[{'text':system}]},
              'contents':[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':m['content']}]} for m in request.messages if m['role']!='system'],
              'generationConfig':{'maxOutputTokens':request.max_output_tokens,'temperature':0,'responseMimeType':'application/json'}}
        if self.config.structured_outputs: body['generationConfig']['responseJsonSchema']=self.transport_schema(request.output_schema)
        return body

    def parse(self, payload):
        try:
            candidate=payload['candidates'][0]
            if candidate.get('finishReason')!='STOP': raise InferenceError('LLM_INCOMPLETE_OUTPUT')
            parts=candidate['content']['parts']
            if any('functionCall' in p for p in parts): raise InferenceError('LLM_TOOL_CALL_FORBIDDEN')
            text=''.join(p['text'] for p in parts if 'text' in p and not p.get('thought'))
            if not text: raise ValueError()
            usage=payload.get('usageMetadata',{})
            measured={dest:usage[src] for src,dest in [('promptTokenCount','input_tokens'),('candidatesTokenCount','output_tokens')] if type(usage.get(src)) is int}
            if type(usage.get('thoughtsTokenCount')) is int and usage['thoughtsTokenCount']>=0:
                measured['output_tokens']=measured.get('output_tokens',0)+usage['thoughtsTokenCount']
            return ProviderResponse(text,payload.get('modelVersion'),measured or None)
        except InferenceError: raise
        except Exception: raise InferenceError('LLM_PROVIDER_PROTOCOL') from None

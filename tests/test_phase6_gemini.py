import unittest
from backend.llm.configuration import ProviderConfig
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.contracts import ProviderRequest,InferenceError
class GeminiTests(unittest.TestCase):
    def setUp(self):self.p=GeminiProvider(ProviderConfig(name='native',kind='gemini',model='configured-model',endpoint='https://example.test/model:generateContent',api_key_env='GEMINI_API_KEY',locality='external'))
    def test_body_native_system_role_and_json_mode(self):
        b=self.p.body(ProviderRequest(({'role':'system','content':'system'},{'role':'user','content':'user'}),{},100,1))
        self.assertEqual(b['systemInstruction']['parts'][0]['text'],'system');self.assertEqual(b['contents'][0]['role'],'user');self.assertEqual(b['generationConfig']['responseMimeType'],'application/json')
    def test_usage_and_native_response(self):
        r=self.p.parse({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{}'}]}}],'usageMetadata':{'promptTokenCount':5,'candidatesTokenCount':3}})
        self.assertEqual(r.text,'{}');self.assertEqual(r.usage,{'input_tokens':5,'output_tokens':3})
    def test_tool_calls_forbidden(self):
        with self.assertRaises(InferenceError):self.p.parse({'candidates':[{'finishReason':'STOP','content':{'parts':[{'functionCall':{'name':'bank_write'}}]}}]})
    def test_truncation_and_bad_response_rejected(self):
        for body in [{},{'candidates':[{'finishReason':'MAX_TOKENS'}]}]:
            with self.assertRaises(InferenceError):self.p.parse(body)

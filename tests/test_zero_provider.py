"""Offline malformed third-party chat response contract; no paid calls."""
import unittest
from backend.llm.configuration import ProviderConfig
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider
from backend.llm.contracts import InferenceError

class ProviderShapeTests(unittest.TestCase):
    def test_non_object_chat_choices_and_message_have_fixed_protocol_error(self):
        provider=OpenAICompatibleProvider(ProviderConfig(name='glm-test',kind='openai_compatible',
            model='deployment-configured',endpoint='https://example.test/chat/completions',locality='external'))
        payloads=[{},[],None,{'choices':[]},{'choices':[None]},{'choices':[[]]},
            {'choices':[{'message':None}]},{'choices':[{'message':[]}]},
            {'choices':[{'message':'provider-secret'}]}]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(InferenceError) as caught:provider.parse(payload)
                self.assertEqual(caught.exception.code,'LLM_PROVIDER_PROTOCOL')
                self.assertFalse(caught.exception.retryable)

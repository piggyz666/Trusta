"""Re-run all original financial acceptance cases under explicit V3, without weakening assertions."""
from dataclasses import replace
import tests.test_acceptance as baseline
from backend.service import TrustaService


class Phase4ExecutionAcceptance(baseline.Acceptance):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, communication_version=3)
        self.service = TrustaService(self.config)

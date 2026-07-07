import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules.gemini_client import GeminiClient
from modules.schemas import Pass1Output


class GeminiClientRepairTests(unittest.TestCase):
    def test_validate_or_repair_uses_exception_in_context(self):
        client = GeminiClient(api_key="test")
        client.client = MagicMock()
        client.client.models.generate_content.return_value = SimpleNamespace(
            text='{"full_text": "ok", "ocr_confidence_notes": [], "risk_points": []}'
        )

        result = client._validate_or_repair('{bad json', Pass1Output, "gemini-test")

        self.assertIsInstance(result, Pass1Output)
        self.assertEqual(result.full_text, "ok")

    def test_run_pass1_uses_response_schema_for_strong_validation(self):
        client = GeminiClient(api_key="test")
        client.client = MagicMock()
        client.client.models.generate_content.return_value = SimpleNamespace(
            text='{"full_text": "ok", "ocr_confidence_notes": [], "risk_points": []}'
        )

        client.run_pass1([b"img"])

        config = client.client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.response_schema, Pass1Output)


if __name__ == "__main__":
    unittest.main()

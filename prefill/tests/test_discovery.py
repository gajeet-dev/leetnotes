from unittest.mock import patch
import json
from pathlib import Path

from django.test import SimpleTestCase

from prefill.providers.discovery import (
    CLAUDE_PRESET_MODELS,
    EFFORT_LEVELS,
    fetch_agy_models,
    get_agy_models,
    load_cached_agy_models,
    parse_agy_models_output,
    save_cached_agy_models,
)


class DiscoveryTests(SimpleTestCase):
    def test_parse_agy_models_output_tab_separated(self):
        sample = (
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
        )
        parsed = parse_agy_models_output(sample)
        self.assertEqual(
            parsed,
            [
                {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)"},
                {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)"},
            ],
        )

    def test_parse_agy_models_output_with_spinner_and_spaces(self):
        sample = (
            "⠋ Fetching available models...\n"
            "gemini-3.8-flash-high     Gemini 3.8 Flash (High)\n"
            "gpt-oss-120b-medium       GPT-OSS 120B (Medium)\n"
        )
        parsed = parse_agy_models_output(sample)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["id"], "gemini-3.8-flash-high")
        self.assertEqual(parsed[0]["name"], "Gemini 3.8 Flash (High)")
        self.assertEqual(parsed[1]["id"], "gpt-oss-120b-medium")

    def test_caching_and_load(self):
        models = [{"id": "m1", "name": "Model 1"}]
        with patch("prefill.providers.discovery.load_cached_agy_models", return_value=models):
            res = get_agy_models(force_refresh=False)
            self.assertEqual(res, models)

    def test_refresh_calls_fetch_and_saves(self):
        models = [{"id": "m2", "name": "Model 2"}]
        with (
            patch("prefill.providers.discovery.fetch_agy_models", return_value=models),
            patch("prefill.providers.discovery.save_cached_agy_models") as mock_save,
        ):
            res = get_agy_models(force_refresh=True)
            self.assertEqual(res, models)
            mock_save.assert_called_once_with(models)

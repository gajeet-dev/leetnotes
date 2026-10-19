import json
import sys

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from prefill.models import AIResponse
from prefill.providers.base import ProviderError, ProviderResult, ProviderUnavailable
from prefill.providers.cli import CliProvider
from prefill.services import generate_for_problem
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission


def fake(code, *args, **kwargs):
    return CliProvider("fake", command=[sys.executable, "-c", code, *args], **kwargs)


ECHO_ENVELOPE = (
    "import sys, json; print(json.dumps({'result': 'got:' + sys.stdin.read()}))"
)


class CliProviderTests(SimpleTestCase):
    def test_stdin_envelope_and_combined_prompt(self):
        result = fake(ECHO_ENVELOPE, envelope_key="result").generate("SYS", "USER")
        self.assertEqual(result.text, "got:SYS\n\n---\n\nUSER")

    def test_system_flag_goes_in_argv_and_user_on_stdin(self):
        code = (
            "import sys, json;"
            "print(json.dumps({'result': json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()})}))"
        )
        provider = fake(code, envelope_key="result", system_prompt_flag="--sys")
        seen = json.loads(provider.generate("SYS", "USER").text)
        self.assertEqual(seen, {"argv": ["--sys", "SYS"], "stdin": "USER"})

    def test_arg_mode_without_envelope(self):
        provider = fake("import sys; print(sys.argv[1])", "{prompt}", prompt_via="arg")
        self.assertEqual(
            provider.generate("SYS", "USER").text.strip(), "SYS\n\n---\n\nUSER"
        )

    def test_model_placeholder(self):
        provider = fake("import sys; print(sys.argv[1])", "{model}", model="sonnet")
        self.assertEqual(provider.generate("S", "U").text.strip(), "sonnet")

    def test_error_envelope_is_unavailable(self):
        code = (
            "import sys, json;"
            "print(json.dumps({'is_error': True, 'result': 'usage limit hit', 'terminal_reason': 'api_error'}));"
            "sys.exit(1)"
        )
        with self.assertRaisesMessage(ProviderUnavailable, "usage limit hit"):
            fake(code, envelope_key="result").generate("S", "U")

    def test_missing_command_is_unavailable(self):
        provider = CliProvider("x", command=["definitely-not-a-real-binary"])
        with self.assertRaises(ProviderUnavailable):
            provider.generate("S", "U")

    def test_timeout_is_a_plain_error(self):
        with self.assertRaises(ProviderError) as ctx:
            fake("import time; time.sleep(5)", timeout=0.5).generate("S", "U")
        self.assertNotIsInstance(ctx.exception, ProviderUnavailable)

    def test_empty_output(self):
        with self.assertRaisesMessage(ProviderError, "no output"):
            fake("pass").generate("S", "U")

    def test_nonzero_exit_includes_stderr(self):
        with self.assertRaisesMessage(ProviderError, "boom"):
            fake("import sys; sys.stderr.write('boom'); sys.exit(2)").generate("S", "U")

    def test_from_settings_ignores_the_type_key(self):
        provider = CliProvider.from_settings(
            "p", {"type": "cli", "command": ["x"], "model": "m"}
        )
        self.assertEqual((provider.name, provider.model), ("p", "m"))

    def test_from_settings_unknown_option_names_the_provider(self):
        with self.assertRaisesMessage(ValueError, 'provider "p"'):
            CliProvider.from_settings(
                "p", {"type": "cli", "command": ["x"], "modle": "m"}
            )

    def test_arg_mode_requires_the_placeholder(self):
        with self.assertRaises(ValueError):
            CliProvider("p", command=["x"], prompt_via="arg")


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def generate(self, system, user):
        if self.error:
            raise self.error
        return ProviderResult(self.text, "fake-1")


class GenerateForProblemTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            content_remote_markdown="Find two numbers.",
        )
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="pass",
            submission_date=timezone.now(),
        )

    def test_valid_output_is_stored_and_active(self):
        response = generate_for_problem(self.problem, FakeProvider(json.dumps(valid())))
        self.assertTrue(response.is_active)
        self.assertEqual(response.source, "provider")
        self.assertEqual(
            (response.provider_name, response.model_name), ("fake", "fake-1")
        )

    def test_invalid_output_is_stored_not_active(self):
        response = generate_for_problem(self.problem, FakeProvider("nope"))
        self.assertEqual(response.status, "invalid")
        self.assertFalse(response.is_active)

    def test_provider_error_stores_nothing(self):
        with self.assertRaises(ProviderError):
            generate_for_problem(self.problem, FakeProvider(error=ProviderError("x")))
        self.assertEqual(AIResponse.objects.count(), 0)

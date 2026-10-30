import json
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils import timezone

from notes.models import NoteRecord
from notes.state import expected_path
from pipeline.handlers import StageDeps
from pipeline.models import ItemStage, TaskItem
from pipeline.note_handlers import generate_prefill, review, save_notes
from pipeline.outcomes import PauseTask, StageError
from pipeline.task_settings import MANUAL_PROVIDER, TaskSettings
from pipeline.tests.helpers import make_item, make_problem, mark_fetched
from prefill.models import AIResponse
from prefill.providers.base import ProviderError, ProviderResult, ProviderUnavailable
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Submission, SyncTask

WAITING = ItemStage.Status.WAITING


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def generate(self, system, user):
        if self.error:
            raise self.error
        return ProviderResult(self.text, "fake-1")


def add_submission(problem) -> None:
    Submission.objects.create(
        problem=problem, lang="python3", code="pass", submission_date=timezone.now()
    )


class PrefillHandlerTests(TestCase):
    def setUp(self):
        self.problem = make_problem("two-sum", 1)
        add_submission(self.problem)
        self.item = make_item(self.problem)

    def run_stage(self, provider, **settings):
        deps = StageDeps(get_provider=lambda name: provider)
        return generate_prefill(
            self.item, TaskSettings(provider="fake", **settings), deps
        )

    def test_the_manual_provider_waits_for_a_pasted_reply(self):
        outcome = generate_prefill(
            self.item, TaskSettings(provider=MANUAL_PROVIDER), StageDeps()
        )
        self.assertEqual(
            (outcome.status, outcome.input_kind),
            (WAITING, TaskItem.InputKind.MANUAL_PREFILL),
        )
        self.assertEqual(AIResponse.objects.count(), 0)

    def test_a_valid_reply_is_saved_and_the_stage_is_done(self):
        outcome = self.run_stage(FakeProvider(json.dumps(valid())))
        self.assertEqual(outcome.status, ItemStage.Status.DONE)
        self.assertEqual(outcome.detail, "fake (fake-1)")
        response = AIResponse.objects.get()
        self.assertEqual((response.source, response.is_active), ("provider", True))

    def test_an_invalid_reply_falls_back_to_manual_and_is_kept(self):
        outcome = self.run_stage(FakeProvider("not json"))
        self.assertEqual(
            (outcome.status, outcome.input_kind),
            (WAITING, TaskItem.InputKind.MANUAL_PREFILL),
        )
        self.assertIn("wasn't valid", outcome.detail)
        self.assertEqual(AIResponse.objects.get().status, "invalid")

    def test_an_invalid_reply_fails_when_fallback_is_off(self):
        with self.assertRaisesMessage(StageError, "wasn't valid"):
            self.run_stage(FakeProvider("not json"), manual_fallback=False)

    def test_a_tool_error_falls_back_to_manual(self):
        outcome = self.run_stage(FakeProvider(error=ProviderError("timed out")))
        self.assertEqual(outcome.status, WAITING)
        self.assertIn("timed out", outcome.detail)

    def test_a_tool_error_fails_the_item_when_fallback_is_off(self):
        with self.assertRaisesMessage(StageError, "timed out"):
            self.run_stage(
                FakeProvider(error=ProviderError("timed out")), manual_fallback=False
            )

    def test_an_unavailable_tool_falls_back_to_manual(self):
        outcome = self.run_stage(FakeProvider(error=ProviderUnavailable("quota")))
        self.assertEqual(outcome.status, WAITING)

    def test_an_unavailable_tool_pauses_the_task_when_fallback_is_off(self):
        with self.assertRaisesMessage(PauseTask, "quota"):
            self.run_stage(
                FakeProvider(error=ProviderUnavailable("quota")), manual_fallback=False
            )

    def test_a_problem_without_data_cannot_be_prompted(self):
        bare = make_problem("bare", 2, content_remote_markdown=None)
        item = make_item(bare)
        deps = StageDeps(get_provider=lambda name: FakeProvider("{}"))
        with self.assertRaisesMessage(StageError, "can't build the prompt"):
            generate_prefill(item, TaskSettings(provider="fake"), deps)

    def test_a_misconfigured_provider_pauses_the_task(self):
        def broken(name):
            raise ImproperlyConfigured("bad option")

        deps = StageDeps(get_provider=broken)
        with self.assertRaisesMessage(PauseTask, "bad option"):
            generate_prefill(self.item, TaskSettings(provider="fake"), deps)


class ReviewHandlerTests(TestCase):
    def setUp(self):
        self.problem = make_problem("two-sum", 1)
        add_submission(self.problem)
        self.item = make_item(self.problem)

    def test_waits_for_approval_when_there_is_an_ai_note(self):
        save_response(
            self.problem, json.dumps(valid()), source=AIResponse.Source.MANUAL
        )
        outcome = review(self.item, TaskSettings(), StageDeps())
        self.assertEqual(
            (outcome.status, outcome.input_kind), (WAITING, TaskItem.InputKind.REVIEW)
        )

    def test_fails_without_an_ai_note(self):
        with self.assertRaisesMessage(StageError, "no AI note"):
            review(self.item, TaskSettings(), StageDeps())


class SaveNotesHandlerTests(TestCase):
    def setUp(self):
        self.problem = make_problem(
            "two-sum",
            1,
            content_local_markdown="Statement.",
            difficulty="Easy",
            tags=[],
            url="https://leetcode.com/problems/two-sum/",
        )
        mark_fetched(self.problem)
        add_submission(self.problem)
        save_response(
            self.problem, json.dumps(valid()), source=AIResponse.Source.MANUAL
        )
        self.item = make_item(self.problem)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name)

    def save(self, **settings):
        settings.setdefault("output_dirs", [str(self.output)])
        return save_notes(self.item, TaskSettings(**settings), StageDeps())

    def solution_path(self) -> Path:
        return expected_path(self.problem, NoteRecord.Kind.SOLUTION, self.output)

    def test_writes_both_notes(self):
        outcome = self.save()
        self.assertEqual(outcome.status, ItemStage.Status.DONE)
        self.assertEqual(
            outcome.detail, "problem note: written 1; solution note: written 1"
        )
        self.assertTrue(self.solution_path().exists())

    def test_a_note_you_edited_is_kept_and_reported(self):
        self.save()
        self.solution_path().write_text("my edits")
        outcome = self.save()
        self.assertEqual(outcome.status, ItemStage.Status.DONE)
        self.assertIn("kept edited 1", outcome.detail)
        self.assertEqual(self.solution_path().read_text(), "my edits")

    def test_replace_edited_replaces_it(self):
        self.save()
        self.solution_path().write_text("my edits")
        self.assertIn("replaced 1", self.save(replace_edited=True).detail)

    def test_a_missing_output_folder_fails_the_stage(self):
        with self.assertRaisesMessage(StageError, "/no/such/folder"):
            self.save(output_dirs=["/no/such/folder"])

    def test_a_note_that_cannot_be_made_fails_the_stage(self):
        SyncTask.objects.filter(problem=self.problem, part=SyncTask.Part.IMAGES).update(
            status=SyncTask.Status.PENDING
        )
        with self.assertRaisesMessage(StageError, "problem note skipped"):
            self.save()

    def test_no_output_folder_pauses_the_task(self):
        with self.assertRaisesMessage(PauseTask, "output folder"):
            self.save(output_dirs=[])

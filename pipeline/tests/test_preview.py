import tempfile
from pathlib import Path

from django.test import override_settings

from notes.models import NoteRecord
from notes.state import expected_path
from pipeline.preview import describe_preview, preview_plan
from pipeline.stages import Stage
from pipeline.tests.helpers import make_problem, mark_fetched
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase


class PreviewTests(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        update_config({"output_dirs": ["/vault"]})
        self.fetched = make_problem("two-sum", 1)
        self.fresh = make_problem("contains-duplicate", 217)
        mark_fetched(self.fetched)

    def test_counts(self):
        preview = preview_plan([self.fetched, self.fresh])
        self.assertEqual(preview.eligible, 2)
        self.assertEqual(preview.needs_fetching, 1)
        self.assertEqual(preview.stage_runs[Stage.FETCH_PROBLEM], 1)
        self.assertEqual(preview.stage_runs[Stage.GENERATE_PREFILL], 2)
        self.assertEqual(preview.stage_runs[Stage.SAVE_NOTES], 2)

    def test_rejections_are_listed_and_not_counted(self):
        unsolved = make_problem("unsolved", 3, solved=False)
        preview = preview_plan([self.fresh, unsolved])
        self.assertEqual(preview.eligible, 1)
        self.assertEqual([r.problem.slug for r in preview.rejected], ["unsolved"])

    def test_edited_notes_are_counted_and_not_when_replacing_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path = expected_path(self.fresh, NoteRecord.Kind.PROBLEM, Path(folder))
            path.parent.mkdir(parents=True)
            path.write_text(
                "an older note"
            )  # exists, but we have no record of writing it
            self.assertEqual(
                preview_plan([self.fresh], {"output_dirs": [folder]}).edited_notes, 1
            )
            replacing = preview_plan(
                [self.fresh], {"output_dirs": [folder], "replace_edited": True}
            )
            self.assertEqual(replacing.edited_notes, 0)

    @override_settings(NOTES_OUTPUT_DIRS=[])
    def test_a_missing_output_folder_is_a_warning(self):
        update_config({"output_dirs": None})
        self.assertIn("No output folder", preview_plan([self.fresh]).warnings[0])

    def test_description(self):
        self.assertEqual(
            describe_preview(preview_plan([self.fetched, self.fresh])),
            "2 problems: 1 to fetch, 2 AI notes to generate.",
        )

    def test_description_for_one_problem_and_for_nothing(self):
        self.assertEqual(
            describe_preview(preview_plan([self.fresh])),
            "1 problem: 1 to fetch, 1 AI note to generate.",
        )
        unsolved = make_problem("unsolved", 3, solved=False)
        self.assertEqual(describe_preview(preview_plan([unsolved])), "Nothing to do.")

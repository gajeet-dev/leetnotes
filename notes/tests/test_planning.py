from notes.planning import plan_write
from notes.policy import NoteAction
from notes.state import NoteState
from notes.tests.helpers import NoteFilesTestCase


class PlanWriteTests(NoteFilesTestCase):
    def plan(self, new_text: str, **options):
        return plan_write(self.problem, self.kind, self.output, new_text, **options)

    def test_new_note_is_written(self):
        plan = self.plan("fresh")
        self.assertEqual((plan.state, plan.action), (NoteState.NEVER_WRITTEN, NoteAction.WRITE))
        self.assertEqual(plan.path, self.path)

    def test_unchanged_note_with_new_text_is_overwritten(self):
        self.write_file("old")
        self.add_record("old")
        self.assertEqual(self.plan("new").action, NoteAction.OVERWRITE)

    def test_unchanged_note_with_identical_text_is_up_to_date(self):
        self.write_file("same\n")
        self.add_record("same\n")
        self.assertEqual(self.plan("same\r\n").action, NoteAction.UP_TO_DATE)

    def test_edited_note_is_skipped_by_default_and_replaceable_on_request(self):
        self.write_file("my edits")
        self.add_record("what we wrote")
        self.assertEqual(self.plan("new").action, NoteAction.SKIP_EDITED)
        self.assertEqual(
            self.plan("new", replace_edited=True).action, NoteAction.BACKUP_AND_OVERWRITE
        )

    def test_older_note_that_already_matches_is_adopted(self):
        self.write_file("identical")
        self.assertEqual(self.plan("identical").action, NoteAction.ADOPT)

    def test_older_note_that_differs_is_protected(self):
        self.write_file("older text")
        self.assertEqual(self.plan("new text").action, NoteAction.SKIP_EDITED)

    def test_missing_note_is_written_again(self):
        self.add_record("gone")
        self.assertEqual(self.plan("fresh").action, NoteAction.WRITE)

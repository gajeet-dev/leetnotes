from django.test import SimpleTestCase

from notes.policy import NoteAction, decide_action
from notes.state import NoteState

# (state, replace_edited, text_matches_file) -> action
CASES = [
    (NoteState.NEVER_WRITTEN, False, False, NoteAction.WRITE),
    (NoteState.MISSING, False, False, NoteAction.WRITE),
    (NoteState.UNCHANGED, False, False, NoteAction.OVERWRITE),
    (NoteState.UNCHANGED, False, True, NoteAction.UP_TO_DATE),
    (NoteState.MODIFIED, False, False, NoteAction.SKIP_EDITED),
    (NoteState.MODIFIED, True, False, NoteAction.BACKUP_AND_OVERWRITE),
    (NoteState.MODIFIED, False, True, NoteAction.ADOPT),
    (NoteState.UNTRACKED_FILE, False, False, NoteAction.SKIP_EDITED),
    (NoteState.UNTRACKED_FILE, True, False, NoteAction.BACKUP_AND_OVERWRITE),
    (NoteState.UNTRACKED_FILE, False, True, NoteAction.ADOPT),
]


class DecideActionTests(SimpleTestCase):
    def test_decision_table(self):
        for state, replace_edited, matches, expected in CASES:
            with self.subTest(
                state=state, replace_edited=replace_edited, matches=matches
            ):
                action = decide_action(
                    state, replace_edited=replace_edited, text_matches_file=matches
                )
                self.assertEqual(action, expected)

    def test_an_edited_note_is_never_replaced_without_asking(self):
        for state in (NoteState.MODIFIED, NoteState.UNTRACKED_FILE):
            action = decide_action(state, replace_edited=False, text_matches_file=False)
            self.assertEqual(action, NoteAction.SKIP_EDITED)

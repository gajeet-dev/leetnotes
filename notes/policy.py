"""What to do with a note, given its state."""

from enum import StrEnum

from .state import NoteState


class NoteAction(StrEnum):
    WRITE = "write"  # nothing there to protect
    OVERWRITE = "overwrite"  # replace our own unedited output
    BACKUP_AND_OVERWRITE = (
        "backup_and_overwrite"  # replace an edited note, after copying it aside
    )
    SKIP_EDITED = "skip_edited"  # you edited it: left alone
    UP_TO_DATE = "up_to_date"  # the new text is identical: nothing to do
    ADOPT = "adopt"  # file already matches: just start tracking it


EDITED_STATES = {NoteState.MODIFIED, NoteState.UNTRACKED_FILE}


def decide_action(
    state: NoteState, *, replace_edited: bool, text_matches_file: bool
) -> NoteAction:
    """Pick the action. An edited note is only replaced when you explicitly ask."""
    if state in EDITED_STATES:
        if text_matches_file:
            return NoteAction.ADOPT
        return (
            NoteAction.BACKUP_AND_OVERWRITE
            if replace_edited
            else NoteAction.SKIP_EDITED
        )
    if state is NoteState.UNCHANGED:
        return NoteAction.UP_TO_DATE if text_matches_file else NoteAction.OVERWRITE
    return NoteAction.WRITE  # never written, or missing

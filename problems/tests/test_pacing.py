from problems.services.sync import PACE_FROM_SETTINGS, resolve_pace
from userconfig import update_config
from userconfig.tests.helpers import DataDirTestCase


class ResolvePaceTests(DataDirTestCase):
    def test_the_default_uses_the_builtin_value(self):
        self.assertEqual(resolve_pace(PACE_FROM_SETTINGS), 5.0)

    def test_the_default_uses_the_saved_value_when_there_is_one(self):
        update_config({"problems_per_minute": 3})
        self.assertEqual(resolve_pace(PACE_FROM_SETTINGS), 3)

    def test_none_still_means_no_pacing(self):
        self.assertIsNone(resolve_pace(None))

    def test_an_explicit_value_is_used_as_given(self):
        self.assertEqual(resolve_pace(12.0), 12.0)

from django.test import SimpleTestCase

from pipeline.note_handlers import review
from pipeline.stage_registry import HANDLERS, handler_for
from pipeline.stages import Stage


class StageRegistryTests(SimpleTestCase):
    def test_every_stage_has_a_handler(self):
        self.assertEqual(set(HANDLERS), set(Stage))

    def test_handler_for_accepts_the_stage_name(self):
        self.assertIs(handler_for("review"), review)
        self.assertIs(handler_for(Stage.REVIEW), review)

    def test_an_unknown_stage_is_an_error(self):
        with self.assertRaises(ValueError):
            handler_for("nonsense")

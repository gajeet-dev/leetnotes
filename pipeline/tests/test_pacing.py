from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from pipeline.pacing import Pacer, seconds_per_problem
from pipeline.stages import Stage
from userconfig import update_config
from userconfig.tests.helpers import DataDirTestCase

A, B = SimpleNamespace(pk=1), SimpleNamespace(pk=2)


def stage(name: Stage) -> SimpleNamespace:
    return SimpleNamespace(stage=name)


class FakeClock:
    def __init__(self, start: float = 100.0):
        self.now, self.waits, self.keep_going = start, [], True

    def clock(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if self.keep_going:
            self.now += seconds
        return self.keep_going


def make_pacer(
    clock: FakeClock, interval: float | None = 12.0, jitter: float = 0.0
) -> Pacer:
    return Pacer(
        lambda: interval, wait=clock.wait, clock=clock.clock, jitter=lambda _: jitter
    )


class PacerTests(SimpleTestCase):
    def setUp(self):
        self.clock = FakeClock()

    def test_the_first_problem_never_waits(self):
        pacer = make_pacer(self.clock)
        self.assertTrue(pacer.before_stage(A, stage(Stage.FETCH_PROBLEM)))
        self.assertEqual(self.clock.waits, [])

    def test_the_next_problem_waits_until_the_interval_has_passed(self):
        pacer = make_pacer(self.clock)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        self.clock.now = 105.0
        self.assertTrue(pacer.before_stage(B, stage(Stage.FETCH_PROBLEM)))
        self.assertEqual(self.clock.waits, [7.0])

    def test_time_that_already_passed_counts(self):
        pacer = make_pacer(self.clock)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        self.clock.now = 130.0
        pacer.before_stage(B, stage(Stage.FETCH_PROBLEM))
        self.assertEqual(self.clock.waits, [])

    def test_jitter_is_added_to_the_wait(self):
        pacer = make_pacer(self.clock, jitter=3.0)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        self.clock.now = 105.0
        pacer.before_stage(B, stage(Stage.FETCH_PROBLEM))
        self.assertEqual(self.clock.waits, [10.0])

    def test_later_fetch_stages_of_the_same_problem_do_not_wait(self):
        pacer = make_pacer(self.clock)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        pacer.before_stage(A, stage(Stage.FETCH_IMAGES))
        pacer.before_stage(A, stage(Stage.FETCH_SUBMISSION))
        self.assertEqual(self.clock.waits, [])

    def test_stages_that_do_not_touch_leetcode_are_never_paced(self):
        pacer = make_pacer(self.clock)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        for name in (Stage.GENERATE_PREFILL, Stage.REVIEW, Stage.SAVE_NOTES):
            self.assertTrue(pacer.before_stage(B, stage(name)))
        self.assertEqual(self.clock.waits, [])

    def test_no_pacing_without_an_interval(self):
        pacer = make_pacer(self.clock, interval=None)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        pacer.before_stage(B, stage(Stage.FETCH_PROBLEM))
        self.assertEqual(self.clock.waits, [])

    def test_a_stop_request_while_waiting_holds_the_stage_back(self):
        pacer = make_pacer(self.clock)
        pacer.before_stage(A, stage(Stage.FETCH_PROBLEM))
        self.clock.keep_going = False
        self.assertFalse(pacer.before_stage(B, stage(Stage.FETCH_PROBLEM)))
        self.clock.keep_going = True  # tried again later: the turn was not claimed
        self.assertTrue(pacer.before_stage(B, stage(Stage.FETCH_PROBLEM)))
        self.assertEqual(len(self.clock.waits), 2)


@override_settings(LEETCODE_PROBLEMS_PER_MINUTE=6.0)
class SecondsPerProblemTests(DataDirTestCase):
    def test_uses_the_env_pace_by_default(self):
        self.assertEqual(seconds_per_problem(), 10.0)

    def test_the_saved_pace_wins(self):
        update_config({"problems_per_minute": 12})
        self.assertEqual(seconds_per_problem(), 5.0)

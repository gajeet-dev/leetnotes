from pathlib import Path

from django.test import override_settings

from pipeline.models import Task
from pipeline.task_settings import (
    MANUAL_PROVIDER,
    TaskSettings,
    TaskSettingsError,
    default_task_settings,
    settings_with_overrides,
    task_settings,
)
from prefill.providers.manage import change_provider
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase


@override_settings(NOTES_OUTPUT_DIRS=[Path("/env/vault")])
class TaskSettingsTests(DataDirDBTestCase):
    def test_defaults_come_from_the_saved_settings(self):
        update_config({"output_dirs": ["/vault"], "review_before_writing": False})
        defaults = default_task_settings()
        self.assertEqual(defaults.provider, "claude")
        self.assertFalse(defaults.review)
        self.assertEqual(defaults.output_dirs, ["/vault"])

    def test_output_folders_fall_back_to_env(self):
        self.assertEqual(default_task_settings().output_dirs, ["/env/vault"])

    def test_overrides_win_over_the_defaults(self):
        settings = settings_with_overrides(
            {"provider": MANUAL_PROVIDER, "review": False, "refetch": True}
        )
        self.assertEqual(settings.provider, MANUAL_PROVIDER)
        self.assertFalse(settings.review)
        self.assertTrue(settings.refetch)
        self.assertFalse(
            settings.regenerate_prefill
        )  # untouched settings keep their defaults

    def test_a_misspelled_setting_is_rejected(self):
        with self.assertRaisesMessage(TaskSettingsError, "reveiw"):
            settings_with_overrides({"reveiw": False})

    def test_an_invalid_value_is_rejected(self):
        with self.assertRaisesMessage(TaskSettingsError, "review"):
            settings_with_overrides({"review": "maybe"})

    def test_an_unknown_provider_lists_the_choices(self):
        with self.assertRaisesMessage(TaskSettingsError, "manual"):
            settings_with_overrides({"provider": "ghost"})

    def test_a_custom_provider_is_accepted(self):
        change_provider("mine", {"type": "cli", "command": ["mytool"]})
        self.assertEqual(settings_with_overrides({"provider": "mine"}).provider, "mine")

    def test_a_task_keeps_its_snapshot_when_the_defaults_change(self):
        update_config({"review_before_writing": True})
        snapshot = settings_with_overrides()
        task = Task.objects.create(settings=snapshot.model_dump())
        update_config({"review_before_writing": False})
        self.assertTrue(task_settings(task).review)
        self.assertEqual(task_settings(task), snapshot)

    def test_an_older_snapshot_with_missing_settings_still_loads(self):
        task = Task.objects.create(settings={"provider": "claude"})
        loaded = task_settings(task)
        self.assertEqual(loaded.provider, "claude")
        self.assertEqual(loaded, TaskSettings(provider="claude"))

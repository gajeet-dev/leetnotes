import os
import sys

from django.apps import AppConfig


def should_auto_start_worker() -> bool:
    # Do not auto-start during management commands (tests, migrations, shell, etc.)
    skip_commands = (
        "test",
        "migrate",
        "makemigrations",
        "collectstatic",
        "shell",
        "check",
    )
    if any(cmd in sys.argv for cmd in skip_commands):
        return False
    # In runserver with reload, only start in the worker process
    if (
        "runserver" in sys.argv
        and "--noreload" not in sys.argv
        and os.environ.get("RUN_MAIN") != "true"
    ):
        return False
    return True


class PipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pipeline"

    def ready(self):
        if should_auto_start_worker():
            from .runtime.worker import start_worker

            start_worker()

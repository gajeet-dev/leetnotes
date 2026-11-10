from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("problems/", views.problems_list, name="problems"),
    path("problems/matching-ids/", views.matching_problem_ids, name="matching_ids"),
    path("problems/sync/", views.sync_selected_problems, name="sync_selected"),
    path("problems/refetch/", views.refetch_selected_problems, name="refetch_selected"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/credentials/", views.save_credentials_view, name="save_credentials"),
    path("settings/test-connection/", views.test_connection_view, name="test_connection"),
    path("settings/general/", views.save_general_settings_view, name="save_general_settings"),
    path("settings/timezone/", views.set_timezone_view, name="set_timezone"),
    path("settings/providers/agy/refresh-models/", views.refresh_agy_models_view, name="refresh_agy_models"),
    path("settings/reset/", views.reset_settings_view, name="reset_settings"),
    path("settings/snapshots/export/", views.export_snapshot_view, name="export_snapshot"),
    path("settings/snapshots/import/", views.import_snapshot_view, name="import_snapshot"),
    path("settings/snapshots/refresh/", views.refresh_snapshots_view, name="refresh_snapshots"),
    path("settings/snapshots/download-db/", views.download_database_file_view, name="download_database_file"),
    path("settings/snapshots/reset-submissions/", views.reset_submissions_view, name="reset_submissions"),
    path("settings/snapshots/clear-notes/", views.clear_notes_records_view, name="clear_notes_records"),
    path("settings/snapshots/reinit-database/", views.reinit_database_view, name="reinit_database"),
]

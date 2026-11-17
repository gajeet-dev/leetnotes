from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("dashboard/", views.dashboard_view, name="dashboard_alias"),
    path(
        "dashboard/sync-leetcode/",
        views.sync_leetcode_data_view,
        name="sync_leetcode_data",
    ),
    path("problems/", views.problems_list, name="problems"),
    path("problems/matching-ids/", views.matching_problem_ids, name="matching_ids"),
    path("problems/sync/", views.sync_selected_problems, name="sync_selected"),
    path("problems/refetch/", views.refetch_selected_problems, name="refetch_selected"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/credentials/", views.save_credentials_view, name="save_credentials"),
    path(
        "settings/test-connection/", views.test_connection_view, name="test_connection"
    ),
    path(
        "settings/general/",
        views.save_general_settings_view,
        name="save_general_settings",
    ),
    path("settings/timezone/", views.set_timezone_view, name="set_timezone"),
    path(
        "settings/providers/agy/refresh-models/",
        views.refresh_agy_models_view,
        name="refresh_agy_models",
    ),
    path("settings/reset/", views.reset_settings_view, name="reset_settings"),
    path(
        "settings/snapshots/export/", views.export_snapshot_view, name="export_snapshot"
    ),
    path(
        "settings/snapshots/import/", views.import_snapshot_view, name="import_snapshot"
    ),
    path(
        "settings/snapshots/refresh/",
        views.refresh_snapshots_view,
        name="refresh_snapshots",
    ),
    path(
        "settings/snapshots/download-db/",
        views.download_database_file_view,
        name="download_database_file",
    ),
    path(
        "settings/snapshots/reset-submissions/",
        views.reset_submissions_view,
        name="reset_submissions",
    ),
    path(
        "settings/snapshots/clear-notes/",
        views.clear_notes_records_view,
        name="clear_notes_records",
    ),
    path(
        "settings/snapshots/reinit-database/",
        views.reinit_database_view,
        name="reinit_database",
    ),
    # Notes Generation Workbench & Problem Selection Catalogue
    path("tasks/", views.tasks_workbench_view, name="tasks"),
    path("tasks/list/", views.tasks_workbench_view, name="tasks_list"),
    path("tasks/new/", views.tasks_catalog_view, name="tasks_new"),
    path("tasks/poll/", views.tasks_poll_view, name="tasks_poll"),
    path(
        "tasks/modal/settings/",
        views.generate_settings_modal_view,
        name="generate_settings_modal",
    ),
    path("tasks/create/", views.tasks_create_view, name="tasks_create"),
    path("tasks/regenerate/", views.tasks_regenerate_view, name="tasks_regenerate"),
    path(
        "tasks/review/approve-all/",
        views.approve_all_reviews_view,
        name="approve_all_reviews",
    ),
    path(
        "tasks/items/<int:item_id>/stage/<str:stage>/modal/",
        views.stage_modal_content_view,
        name="stage_modal",
    ),
    path(
        "tasks/items/<int:item_id>/submission/choice/",
        views.resolve_submission_choice_view,
        name="resolve_submission_choice",
    ),
    path(
        "tasks/items/<int:item_id>/prefill/",
        views.submit_prefill_view,
        name="submit_prefill",
    ),
    path(
        "tasks/items/<int:item_id>/prefill/switch-provider/",
        views.switch_provider_prefill_view,
        name="switch_provider_prefill",
    ),
    path(
        "tasks/items/<int:item_id>/prefill/select-response/<int:response_id>/",
        views.select_prefill_response_view,
        name="select_prefill_response",
    ),
    path(
        "tasks/items/<int:item_id>/prefill/preview-response/<int:response_id>/",
        views.preview_ai_response_view,
        name="preview_ai_response",
    ),
    path(
        "tasks/items/<int:item_id>/prefill/delete-response/<int:response_id>/",
        views.delete_ai_response_view,
        name="delete_ai_response",
    ),
    path(
        "tasks/items/<int:item_id>/review/approve/",
        views.approve_review_view,
        name="approve_review",
    ),
    path(
        "tasks/items/<int:item_id>/review/reject/",
        views.reject_review_view,
        name="reject_review",
    ),
    path(
        "tasks/status-batch/", views.tasks_status_batch_view, name="tasks_status_batch"
    ),
    path("tasks/items/<int:item_id>/retry/", views.retry_item_view, name="retry_item"),
    path("tasks/items/<int:item_id>/pause/", views.pause_item_view, name="pause_item"),
    path(
        "tasks/items/<int:item_id>/resume/", views.resume_item_view, name="resume_item"
    ),
    path(
        "tasks/items/<int:item_id>/cancel/", views.cancel_item_view, name="cancel_item"
    ),
]

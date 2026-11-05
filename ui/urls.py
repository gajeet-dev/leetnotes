from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sync/", views.sync_leetcode, name="sync_leetcode"),
    path("generate/", views.generate_notes_view, name="generate_notes"),
    path("tasks/", views.tasks_list, name="tasks_list"),
    path("tasks/live-status/", views.tasks_live_status, name="tasks_live_status"),
    path("tasks/preview-modal/", views.task_preview_modal, name="task_preview_modal"),
    path("tasks/create/", views.create_task_action, name="create_task"),
    path("tasks/<int:task_id>/", views.task_detail, name="task_detail"),
    path("tasks/<int:task_id>/live-status/", views.task_live_status, name="task_live_status"),
    path("tasks/<int:task_id>/pause/", views.task_pause_action, name="task_pause"),
    path("tasks/<int:task_id>/resume/", views.task_resume_action, name="task_resume"),
    path("tasks/<int:task_id>/cancel/", views.task_cancel_action, name="task_cancel"),
    path("tasks/<int:task_id>/approve-all/", views.task_approve_all_reviews, name="task_approve_all"),
    path("tasks/<int:task_id>/pin-all/", views.task_pin_all_submissions, name="task_pin_all"),
    path("tasks/<int:task_id>/batch-pin/", views.task_batch_pin_submissions, name="task_batch_pin"),
    path("tasks/items/<int:item_id>/manual-input/", views.task_item_manual_input, name="item_manual_input"),
    path("tasks/items/<int:item_id>/retry/", views.task_item_retry, name="item_retry"),
    path("tasks/items/<int:item_id>/approve/", views.task_item_approve_review, name="item_approve_review"),
    path("tasks/items/<int:item_id>/reject/", views.task_item_reject_review, name="item_reject_review"),
    path("submissions/<int:submission_id>/toggle-pin/", views.toggle_submission_pin, name="toggle_submission_pin"),
    path("tasks/progress/", views.tasks_progress_partial, name="tasks_progress"),
    path("problems/", views.problems_list, name="problems"),
]

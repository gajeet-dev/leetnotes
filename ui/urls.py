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
]

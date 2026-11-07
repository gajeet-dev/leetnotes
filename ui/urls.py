from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("problems/", views.problems_list, name="problems"),
    path("problems/matching-ids/", views.matching_problem_ids, name="matching_ids"),
    path("problems/sync/", views.sync_selected_problems, name="sync_selected"),
    path("problems/refetch/", views.refetch_selected_problems, name="refetch_selected"),
]

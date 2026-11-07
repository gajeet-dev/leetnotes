from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("problems/", views.problems_list, name="problems"),
]

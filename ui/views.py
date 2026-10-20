from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def dashboard(request):
    return render(request, "ui/dashboard.html")

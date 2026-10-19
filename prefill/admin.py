import json

from django.contrib import admin, messages
from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from problems.admin import ProblemAdmin
from problems.models import Problem

from .forms import ManualResponseForm
from .models import AIResponse
from .prompt_builder import PromptInputError, build_prompt
from .services import activate, import_manual_response


@admin.register(AIResponse)
class AIResponseAdmin(admin.ModelAdmin):
    show_facets = admin.ShowFacets.ALWAYS
    list_display = (
        "problem",
        "source",
        "provider_name",
        "status",
        "is_active",
        "created_at",
    )
    list_filter = ("status", "is_active", "source", "provider_name")
    search_fields = ("problem__slug", "problem__title")
    list_select_related = ("problem",)
    actions = ["make_active"]

    fields = (
        "problem",
        "source",
        "provider_name",
        "model_name",
        "status",
        "is_active",
        "error",
        "parsed_pretty",
        "raw_text",
        "prompt_version",
        "input_hash",
        "created_at",
    )
    readonly_fields = (
        fields  # responses are records: create via the paste page or a provider
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description="Parsed JSON")
    def parsed_pretty(self, obj):
        if not obj.parsed:
            return "-"
        return format_html(
            "<pre>{}</pre>", json.dumps(obj.parsed, indent=2, ensure_ascii=False)
        )

    @admin.action(description="Make selected response the active one")
    def make_active(self, request, queryset):
        done = 0
        for response in queryset.order_by("created_at"):  # newest wins per problem
            if response.status == AIResponse.Status.VALID:
                activate(response)
                done += 1
        skipped = queryset.count() - done
        self.message_user(
            request,
            f"{done} activated" + (f", {skipped} skipped (invalid)" if skipped else ""),
            messages.SUCCESS if done else messages.WARNING,
        )

    # --- the manual paste page ---

    def get_urls(self):
        custom = [
            path(
                "manual/<int:problem_id>/",
                self.admin_site.admin_view(self.manual_view),
                name="prefill_airesponse_manual",
            ),
        ]
        return custom + super().get_urls()

    def manual_view(self, request, problem_id):
        problem = get_object_or_404(Problem, pk=problem_id)
        try:
            prompt = build_prompt(problem)
        except PromptInputError as exc:
            self.message_user(request, f"Can't build a prompt: {exc}.", messages.ERROR)
            return redirect("admin:problems_problem_changelist")

        form = ManualResponseForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            response = import_manual_response(
                problem,
                form.cleaned_data["raw_text"],
                label=form.cleaned_data["label"],
                prompt=prompt,
            )
            self.message_user(
                request, "Saved and set as the active AI note.", messages.SUCCESS
            )
            return redirect(
                reverse("admin:prefill_airesponse_change", args=[response.pk])
            )

        context = {
            **self.admin_site.each_context(request),
            "title": f"AI note for {problem}",
            "opts": self.model._meta,
            "problem": problem,
            "prompt_text": prompt.combined,
            "active": problem.ai_responses.filter(is_active=True).first(),
            "form": form,
        }
        return TemplateResponse(request, "admin/prefill/manual_response.html", context)


# --- extend the Problem admin without touching the problems app ---


class AINoteFilter(admin.SimpleListFilter):
    title = "AI note"
    parameter_name = "ai_note"

    def lookups(self, request, model_admin):
        return [("yes", "Has an active AI note"), ("no", "No AI note yet")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(has_ai_note=True)
        if self.value() == "no":
            return queryset.filter(has_ai_note=False)
        return queryset


admin.site.unregister(Problem)


@admin.register(Problem)
class ProblemWithAIAdmin(ProblemAdmin):
    actions = [*ProblemAdmin.actions, "prefill_manual"]
    list_display = (*ProblemAdmin.list_display, "ai_note")
    list_filter = (*ProblemAdmin.list_filter, AINoteFilter)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                has_ai_note=Exists(
                    AIResponse.objects.filter(problem=OuterRef("pk"), is_active=True)
                )
            )
        )

    @admin.display(description="AI note", boolean=True, ordering="has_ai_note")
    def ai_note(self, obj):
        return obj.has_ai_note

    @admin.action(description="AI note: paste a manual response (select one problem)")
    def prefill_manual(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one problem.", messages.WARNING)
            return None
        return redirect("admin:prefill_airesponse_manual", problem_id=queryset.get().pk)

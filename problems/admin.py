from django.contrib import admin
from django.db.models import Count, Q
from django.utils.html import format_html

from .models import Problem, ProblemImage, Submission, SyncTask


class AlwaysFacetedAdmin(admin.ModelAdmin):
    show_facets = admin.ShowFacets.ALWAYS


class SyncStatusFilter(admin.SimpleListFilter):
    title = "sync status"
    parameter_name = "sync"

    def lookups(self, request, model_admin):
        return [
            ("done", "Fully synced"),
            ("pending", "Has pending parts"),
            ("failed", "Has failed parts"),
            ("none", "No sync needed (paid-only)"),
        ]

    def queryset(self, request, queryset):
        match self.value():
            case "done":
                return queryset.filter(
                    total_count__gt=0, pending_count=0, failed_count=0
                )
            case "pending":
                return queryset.filter(pending_count__gt=0)
            case "failed":
                return queryset.filter(failed_count__gt=0)
            case "none":
                return queryset.filter(total_count=0)
        return queryset


class ProblemImageInline(admin.TabularInline):
    model = ProblemImage
    extra = 0
    fields = ("preview", "source_url", "file")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.pk and obj.file:
            return format_html('<img src="{}" style="max-height: 80px;">', obj.file.url)
        return "-"


class SubmissionInline(admin.TabularInline):
    model = Submission
    extra = 0
    fields = ("lang", "submission_date", "pinned", "code")
    show_change_link = True


@admin.register(Problem)
class ProblemAdmin(AlwaysFacetedAdmin):
    list_display = (
        "problem",
        "difficulty",
        "solved",
        "category",
        "paid_only",
        "has_images",
        "updated_at",
    )
    list_filter = (
        SyncStatusFilter,
        "difficulty",
        "solved",
        "category",
        "paid_only",
        "has_images",
    )
    list_display_links = ("problem",)
    search_fields = ("title", "slug", "leetcode_id")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ProblemImageInline, SubmissionInline]

    @admin.display(description="Problem", ordering="leetcode_id")
    def problem(self, obj):
        name = obj.title or obj.slug
        if obj.leetcode_id is None:
            return name
        return f"{obj.leetcode_id}. {name}"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                total_count=Count("sync_tasks"),
                pending_count=Count(
                    "sync_tasks", filter=Q(sync_tasks__status=SyncTask.Status.PENDING)
                ),
                failed_count=Count(
                    "sync_tasks", filter=Q(sync_tasks__status=SyncTask.Status.FAILED)
                ),
            )
        )

    @admin.display(description="Sync", ordering="pending_count")
    def sync_status(self, obj):
        if not obj.total_count:
            return "n/a"
        if obj.failed_count:
            return f"failed ({obj.failed_count})"
        if obj.pending_count:
            return f"pending ({obj.pending_count}/{obj.total_count})"
        return "done"


@admin.register(Submission)
class SubmissionAdmin(AlwaysFacetedAdmin):
    list_display = ("problem", "lang", "submission_date", "pinned")
    list_filter = ("lang", "pinned")
    search_fields = ("problem__slug", "problem__title")
    list_select_related = ("problem",)
    date_hierarchy = "submission_date"


@admin.register(ProblemImage)
class ProblemImageAdmin(AlwaysFacetedAdmin):
    list_display = ("problem", "source_url", "file")
    search_fields = ("problem__slug", "source_url")
    list_select_related = ("problem",)


class SyncTaskInline(admin.TabularInline):
    model = SyncTask
    extra = 0
    fields = ("part", "status", "attempts", "completed_at", "last_error")
    readonly_fields = ("part", "attempts", "completed_at", "last_error")
    can_delete = False
    max_num = 3

    def has_add_permission(self, request, obj=None):
        return False  # tasks are created by discovery, not by hand


@admin.register(SyncTask)
class SyncTaskAdmin(AlwaysFacetedAdmin):
    list_display = ("problem", "part", "status", "attempts", "completed_at")
    list_filter = ("part", "status")
    search_fields = ("problem__slug", "problem__title")
    list_select_related = ("problem",)

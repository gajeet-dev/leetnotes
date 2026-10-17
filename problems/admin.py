from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import format_html

from leetcode.bootstrap import get_client
from leetcode.exceptions import LeetCodeError

from .models import Problem, ProblemImage, Submission, SyncTask
from .services.runner import start_background_sync
from .services.sync import discover, mark_for_refetch


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
        "sync_status",
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
    actions = (
        "sync_selected",
        "sync_selected_retry_failed",
        "refetch_content",
        "refetch_images",
        "refetch_submissions",
    )

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

    def _start_sync(self, request, queryset, *, retry_failed: bool):
        ids = list(queryset.values_list("pk", flat=True))
        statuses = [SyncTask.Status.PENDING]
        if retry_failed:
            statuses.append(SyncTask.Status.FAILED)

        runnable = list(
            SyncTask.objects.filter(problem_id__in=ids, status__in=statuses)
            .values_list("problem_id", flat=True)
            .distinct()
        )
        if not runnable:
            self.message_user(
                request,
                f"Nothing to sync: all {len(ids)} selected problem(s) are "
                "already synced.",
                messages.INFO,
            )
            return

        if start_background_sync(runnable, retry_failed=retry_failed):
            self.message_user(
                request,
                f"Started syncing {len(runnable)} of {len(ids)} selected "
                "problem(s) in the background. Refresh to see progress.",
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "A sync is already running. Wait for it to finish.",
                messages.WARNING,
            )

    def _refetch(self, request, queryset, part: SyncTask.Part):
        result = mark_for_refetch(list(queryset.values_list("pk", flat=True)), part)

        notes = [f"{result.tasks_reset} task(s) reset to pending"]
        if result.pinned_skipped:
            notes.append(
                f"{result.pinned_skipped} skipped because the solution is "
                "pinned (unpin it first)"
            )
        if result.missing:
            notes.append(
                f"{result.missing} have no {part} task (unsolved or paid-only)"
            )

        level = (
            messages.WARNING
            if result.pinned_skipped or result.missing
            else messages.SUCCESS
        )
        self.message_user(
            request,
            ". ".join(notes) + ". Now run a sync action to fetch them.",
            level,
        )

    @admin.action(description="Sync selected problems (pending parts)")
    def sync_selected(self, request, queryset):
        self._start_sync(request, queryset, retry_failed=False)

    @admin.action(description="Sync selected problems (include failed parts)")
    def sync_selected_retry_failed(self, request, queryset):
        self._start_sync(request, queryset, retry_failed=True)

    @admin.action(description="Mark content for refetch (also resets images)")
    def refetch_content(self, request, queryset):
        self._refetch(request, queryset, SyncTask.Part.CONTENT)

    @admin.action(description="Mark images for refetch")
    def refetch_images(self, request, queryset):
        self._refetch(request, queryset, SyncTask.Part.IMAGES)

    @admin.action(description="Mark submissions for refetch")
    def refetch_submissions(self, request, queryset):
        self._refetch(request, queryset, SyncTask.Part.SUBMISSIONS)

    def get_urls(self):
        custom = [
            path(
                "discover/",
                self.admin_site.admin_view(self.discover_view),
                name="problems_problem_discover",
            ),
        ]
        return custom + super().get_urls()  # custom first, or the pk route swallows it

    def discover_view(self, request):
        if request.method != "POST" or not self.has_change_permission(request):
            raise PermissionDenied

        try:
            result = discover(get_client())
        except LeetCodeError as exc:
            self.message_user(request, f"Discovery failed: {exc}", messages.ERROR)
        else:
            if not (result.new_problems or result.newly_solved or result.new_tasks):
                self.message_user(
                    request,
                    f"Nothing new: all {result.total} problems are already known.",
                    messages.INFO,
                )
            else:
                self.message_user(
                    request,
                    f"Discovery complete: {result.new_problems} new problem(s), "
                    f"{result.newly_solved} newly solved, "
                    f"{result.new_tasks} new task(s) created. "
                    "Select problems and run a sync action to fetch them.",
                    messages.SUCCESS,
                )
        return redirect("admin:problems_problem_changelist")


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

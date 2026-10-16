from django.contrib import admin
from django.utils.html import format_html

from .models import Problem, ProblemImage, Submission, SyncTask


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
class ProblemAdmin(admin.ModelAdmin):
    list_display = (
        "leetcode_id",
        "title",
        "difficulty",
        "category",
        "has_images",
        "updated_at",
    )
    list_filter = ("difficulty", "category", "has_images")
    search_fields = ("title", "slug", "leetcode_id")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ProblemImageInline, SubmissionInline]


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("problem", "lang", "submission_date", "pinned")
    list_filter = ("lang", "pinned")
    search_fields = ("problem__slug", "problem__title")
    list_select_related = ("problem",)
    date_hierarchy = "submission_date"


@admin.register(ProblemImage)
class ProblemImageAdmin(admin.ModelAdmin):
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
class SyncTaskAdmin(admin.ModelAdmin):
    list_display = ("problem", "part", "status", "attempts", "completed_at")
    list_filter = ("part", "status")
    search_fields = ("problem__slug", "problem__title")
    list_select_related = ("problem",)

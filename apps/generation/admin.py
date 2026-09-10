from django.contrib import admin

from apps.generation.models import Asset, Generation


class AssetInline(admin.TabularInline):
    model = Asset
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Generation)
class GenerationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "category", "status", "created_at")
    list_filter = ("category", "status", "adapter")
    search_fields = ("prompt", "model_slug", "task_id", "user__username")
    inlines = [AssetInline]

from django.contrib import admin

from apps.chat.models import Attachment, Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "status", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "user", "model_name", "archived", "updated_at")
    list_filter = ("archived", "model_slug")
    search_fields = ("title", "user__username")
    inlines = [MessageInline]


admin.site.register(Attachment)

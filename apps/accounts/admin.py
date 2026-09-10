from django.contrib import admin

from apps.accounts.models import UserSettings


@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "has_kie_key", "telegram_connected", "updated_at")
    search_fields = ("user__username", "user__email")
    # Ciphertext is meaningless in a form, and showing it invites pasting it out.
    exclude = ("kie_api_key_encrypted", "telegram_bot_token_encrypted")

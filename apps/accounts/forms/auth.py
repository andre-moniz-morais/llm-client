"""Registration and settings forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from apps.accounts.models import UserSettings

User = get_user_model()


class StyledFormMixin:
    """Give every widget the classes the site's inputs are styled with."""

    default_widget_class = "field-input"

    def style_fields(self) -> None:
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {self.default_widget_class}".strip()
            if field.label and not widget.attrs.get("placeholder"):
                widget.attrs["placeholder"] = str(field.label)


class LoginForm(StyledFormMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class RegistrationForm(StyledFormMixin, UserCreationForm):
    email = forms.EmailField(required=False, help_text="Optional, used only for account recovery.")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def clean_email(self) -> str:
        email = (self.cleaned_data.get("email") or "").strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already uses that email address.")
        return email


class CredentialsForm(StyledFormMixin, forms.Form):
    """The provider API key.

    The stored key is never rendered back into the page, so an empty submission
    means "leave it as it is" rather than "clear it"; clearing is an explicit
    checkbox.
    """

    kie_api_key = forms.CharField(
        label="Provider API key",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"placeholder": "Paste a new key to replace the stored one", "autocomplete": "off"},
        ),
        help_text="Create one at kie.ai/api-key. Stored encrypted, never shown again.",
    )
    remove_kie_api_key = forms.BooleanField(
        label="Remove the stored key",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def apply_to(self, user_settings: UserSettings) -> list[str]:
        """Write the submitted changes, returning the fields that changed."""
        changed = []
        if self.cleaned_data.get("remove_kie_api_key"):
            user_settings.kie_api_key = ""
            changed.append("kie_api_key_encrypted")
        elif self.cleaned_data.get("kie_api_key"):
            user_settings.kie_api_key = self.cleaned_data["kie_api_key"]
            changed.append("kie_api_key_encrypted")
        return changed


class BrowserNotificationsForm(StyledFormMixin, forms.Form):
    """Whether the chat page should raise a desktop notification on a reply.

    Only the preference lives here.  Whether a notification actually appears is
    also up to the browser's permission prompt, which the settings page asks for
    in JavaScript because only a real user gesture may trigger it.
    """

    browser_notifications_enabled = forms.BooleanField(
        label="Notify me in this browser when a chat reply arrives",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def apply_to(self, user_settings: UserSettings) -> list[str]:
        enabled = self.cleaned_data.get("browser_notifications_enabled", False)
        if enabled == user_settings.browser_notifications_enabled:
            return []
        user_settings.browser_notifications_enabled = enabled
        return ["browser_notifications_enabled"]


class TelegramForm(StyledFormMixin, forms.Form):
    """The Telegram bridge: a bot token plus the chat to post into."""

    telegram_bot_token = forms.CharField(
        label="Telegram bot token",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"placeholder": "123456789:ABC...", "autocomplete": "off"},
        ),
        help_text="Create a bot with @BotFather and paste the token it gives you.",
    )
    telegram_chat_id = forms.CharField(
        label="Chat ID",
        required=False,
        strip=True,
        help_text="Leave empty and press Detect after messaging your bot.",
    )
    telegram_notifications_enabled = forms.BooleanField(
        label="Send me every response on Telegram",
        required=False,
    )
    remove_telegram = forms.BooleanField(label="Disconnect Telegram", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def apply_to(self, user_settings: UserSettings) -> list[str]:
        changed = []
        if self.cleaned_data.get("remove_telegram"):
            user_settings.telegram_bot_token = ""
            user_settings.telegram_chat_id = ""
            return ["telegram_bot_token_encrypted", "telegram_chat_id"]

        if self.cleaned_data.get("telegram_bot_token"):
            user_settings.telegram_bot_token = self.cleaned_data["telegram_bot_token"]
            changed.append("telegram_bot_token_encrypted")

        chat_id = self.cleaned_data.get("telegram_chat_id", "")
        if chat_id != user_settings.telegram_chat_id:
            user_settings.telegram_chat_id = chat_id
            changed.append("telegram_chat_id")

        enabled = self.cleaned_data.get("telegram_notifications_enabled", False)
        if enabled != user_settings.telegram_notifications_enabled:
            user_settings.telegram_notifications_enabled = enabled
            changed.append("telegram_notifications_enabled")
        return changed

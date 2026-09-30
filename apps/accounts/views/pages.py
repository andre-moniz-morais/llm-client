"""Sign-in, registration and the settings page."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.forms.auth import (
    BrowserNotificationsForm,
    CredentialsForm,
    LoginForm,
    RegistrationForm,
    TelegramForm,
)
from apps.accounts.models import UserSettings
from apps.common.services import kie, telegram


class SignInView(LoginView):
    template_name = "pages/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class SignOutView(LogoutView):
    pass


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("chat:index")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome. Add your API key to start generating.")
        return redirect("accounts:settings")

    return render(request, "pages/register.html", {"form": form})


def _settings_for(request: HttpRequest) -> UserSettings:
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    return user_settings


@login_required
def settings_page(request: HttpRequest) -> HttpResponse:
    user_settings = _settings_for(request)

    credentials_form = CredentialsForm()
    telegram_form = TelegramForm(
        initial={
            "telegram_chat_id": user_settings.telegram_chat_id,
            "telegram_notifications_enabled": user_settings.telegram_notifications_enabled,
        }
    )
    browser_form = BrowserNotificationsForm(
        initial={"browser_notifications_enabled": user_settings.browser_notifications_enabled}
    )

    if request.method == "POST":
        section = request.POST.get("section")
        if section == "credentials":
            credentials_form = CredentialsForm(request.POST)
            if credentials_form.is_valid():
                changed = credentials_form.apply_to(user_settings)
                if changed:
                    user_settings.save(update_fields=changed + ["updated_at"])
                    messages.success(request, "API key updated.")
                else:
                    messages.info(request, "Nothing to change.")
                return redirect("accounts:settings")
        elif section == "telegram":
            telegram_form = TelegramForm(request.POST)
            if telegram_form.is_valid():
                was_connected = user_settings.telegram_connected
                changed = telegram_form.apply_to(user_settings)
                if changed:
                    user_settings.save(update_fields=changed + ["updated_at"])
                if user_settings.telegram_awaiting_chat:
                    # Most people press Start before pasting the token, so the
                    # chat is usually already there to be read.
                    _connect_telegram(request, user_settings, quiet=True)
                elif user_settings.telegram_connected and not was_connected:
                    _confirm_telegram(request, user_settings)
                elif changed:
                    messages.success(request, "Telegram settings updated.")
                else:
                    messages.info(request, "Nothing to change.")
                return redirect("accounts:settings")
        elif section == "browser":
            browser_form = BrowserNotificationsForm(request.POST)
            if browser_form.is_valid():
                changed = browser_form.apply_to(user_settings)
                if changed:
                    user_settings.save(update_fields=changed + ["updated_at"])
                    messages.success(request, "Notification settings updated.")
                else:
                    messages.info(request, "Nothing to change.")
                return redirect("accounts:settings")

    return render(
        request,
        "pages/settings.html",
        {
            "user_settings": user_settings,
            "credentials_form": credentials_form,
            "telegram_form": telegram_form,
            "browser_form": browser_form,
        },
    )


@login_required
@require_POST
def verify_key(request: HttpRequest) -> HttpResponse:
    """Check the stored API key and report the credit balance."""
    user_settings = _settings_for(request)
    try:
        client = kie.client_for(request.user)
        credits = client.credits()
    except kie.MissingApiKey:
        messages.error(request, "Add an API key first.")
        return redirect("accounts:settings")
    except kie.KieError as exc:
        messages.error(request, exc.message)
        return redirect("accounts:settings")

    if credits is None:
        messages.warning(request, "The key works, but no credit balance was reported.")
    else:
        messages.success(request, f"Key is valid. {credits} credits remaining.")
    return redirect("accounts:settings")


def _connect_telegram(request: HttpRequest, user_settings: UserSettings, *, quiet: bool = False) -> bool:
    """Read the chat id from the bot's updates and store it.

    ``quiet`` is for the automatic attempt right after a token is saved, where
    "nobody has messaged the bot yet" is the expected state rather than an
    error, so it becomes the next instruction instead.
    """
    token = user_settings.telegram_bot_token
    try:
        chat_id = telegram.resolve_chat_id(token)
    except telegram.TelegramError as exc:
        if quiet:
            bot = f"@{user_settings.telegram_bot_username}" if user_settings.telegram_bot_username else "your bot"
            messages.info(
                request,
                f"Bot token saved. Open {bot} on Telegram and press Start, then press Detect chat ID.",
            )
        else:
            messages.error(request, str(exc))
        return False

    user_settings.telegram_chat_id = chat_id
    user_settings.save(update_fields=["telegram_chat_id", "updated_at"])
    _confirm_telegram(request, user_settings)
    return True


def _confirm_telegram(request: HttpRequest, user_settings: UserSettings) -> None:
    """Prove the bridge works end to end by sending through it."""
    bot = f"@{user_settings.telegram_bot_username}" if user_settings.telegram_bot_username else "your bot"
    sent = telegram.send_message(
        user_settings.telegram_bot_token,
        user_settings.telegram_chat_id,
        "✅ CRAFT is connected. Responses will be sent here.",
    )
    if sent:
        messages.success(request, f"Connected to {bot}. A confirmation was sent to Telegram.")
    else:
        messages.warning(
            request,
            f"Saved, but {bot} could not post to chat {user_settings.telegram_chat_id}. "
            "Check the chat ID, or press Start in the bot's chat and Detect again.",
        )


@login_required
@require_POST
def detect_telegram_chat(request: HttpRequest) -> HttpResponse:
    """Look up the chat id from the bot's pending updates."""
    user_settings = _settings_for(request)
    if not user_settings.telegram_bot_token:
        messages.error(request, "Save a bot token first.")
        return redirect("accounts:settings")

    if not user_settings.telegram_bot_username:
        # Rows saved before the username was recorded.
        try:
            bot = telegram.describe_bot(user_settings.telegram_bot_token)
        except telegram.TelegramError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:settings")
        user_settings.telegram_bot_username = bot.get("username", "")
        user_settings.save(update_fields=["telegram_bot_username", "updated_at"])

    _connect_telegram(request, user_settings)
    return redirect("accounts:settings")


@login_required
@require_POST
def send_test_message(request: HttpRequest) -> HttpResponse:
    user_settings = _settings_for(request)
    if not user_settings.telegram_connected:
        messages.error(request, "Connect a bot token and chat id first.")
        return redirect("accounts:settings")

    sent = telegram.send_message(
        user_settings.telegram_bot_token,
        user_settings.telegram_chat_id,
        "✅ CRAFT is connected.",
    )
    if sent:
        messages.success(request, "Test message sent.")
    else:
        messages.error(request, "Telegram rejected the message. Check the token and chat id.")
    return redirect("accounts:settings")

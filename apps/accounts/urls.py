from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.SignInView.as_view(), name="login"),
    path("logout/", views.SignOutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/verify-key/", views.verify_key, name="verify-key"),
    path("settings/telegram/detect/", views.detect_telegram_chat, name="telegram-detect"),
    path("settings/telegram/test/", views.send_test_message, name="telegram-test"),
]

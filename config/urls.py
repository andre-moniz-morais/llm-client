"""URL map.

Pages return HTML (and HTML fragments for the interactive parts); ``/api/``
exposes the same data as JSON through DRF ViewSets.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView
from rest_framework.routers import DefaultRouter

from apps.accounts.views import UserSettingsViewSet
from apps.catalog.views import ModelViewSet
from apps.chat.views import ConversationViewSet, MessageViewSet
from apps.generation.views import GenerationViewSet


def healthz(_request) -> HttpResponse:
    """Liveness probe for the container healthcheck and any proxy in front.

    Deliberately does not touch the database: this answers "is this process
    serving requests", which is the question an orchestrator restarts on.
    """
    return HttpResponse("ok", content_type="text/plain")


router = DefaultRouter()
router.register("models", ModelViewSet, basename="model")
router.register("conversations", ConversationViewSet, basename="conversation")
router.register("messages", MessageViewSet, basename="message")
router.register("generations", GenerationViewSet, basename="generation")
router.register("settings", UserSettingsViewSet, basename="settings")

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="chat:index", permanent=False)),
    path("healthz", healthz, name="healthz"),
    path("chat/", include("apps.chat.urls")),
    path("studio/", include("apps.generation.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("api/", include((router.urls, "api"))),
    path("api/auth/", include("rest_framework.urls")),
    path("admin/", admin.site.urls),
    # Progressive web app plumbing, served from the template engine so they can
    # reference hashed static URLs.
    path(
        "manifest.webmanifest",
        TemplateView.as_view(
            template_name="pwa/manifest.webmanifest",
            content_type="application/manifest+json",
        ),
        name="manifest",
    ),
    path(
        "service-worker.js",
        TemplateView.as_view(
            template_name="pwa/service-worker.js",
            content_type="application/javascript",
        ),
        name="service-worker",
    ),
    path(
        "offline/",
        TemplateView.as_view(template_name="pages/offline.html"),
        name="offline",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

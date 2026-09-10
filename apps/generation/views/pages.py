"""The image, video and music pages, and the fragments they poll."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from apps.catalog.services import catalog
from apps.common.services import html as html_service
from apps.common.services import kie
from apps.generation.models import Generation
from apps.generation.services import engine, inputs

STUDIO_CATEGORIES = {"image", "video", "music"}


def _fragment(html: str, status: int = 200) -> HttpResponse:
    return HttpResponse(html, content_type="text/html; charset=utf-8", status=status)


def _resolve_selection(request: HttpRequest, category: str):
    """Pick which model the page opens on, and load its schema.

    Preference order: the query string, then the user's last choice, then
    whatever the catalog lists first for this category.
    """
    preferred = request.GET.get("model") or request.user.settings.default_model_for(category)
    spec, slug = catalog.resolve_spec(category, preferred)
    return catalog.models_for(category), slug, spec


@login_required
def studio(request: HttpRequest, category: str) -> HttpResponse:
    """A generation page. The model list is fetched as the page loads."""
    if category not in STUDIO_CATEGORIES:
        raise Http404(f"No such category: {category}")

    models, selected_slug, spec = _resolve_selection(request, category)

    history = Generation.objects.filter(user=request.user, category=category).prefetch_related(
        "assets"
    )
    page = Paginator(history, 12).get_page(request.GET.get("page"))

    return render(
        request,
        "pages/studio.html",
        {
            "category": category,
            "category_label": catalog.CATEGORY_LABELS[category],
            "models": models,
            "providers": catalog.providers_for(category),
            "selected_slug": selected_slug,
            "spec": spec,
            "generations": page,
            "has_key": request.user.settings.has_kie_key,
        },
    )


@login_required
def model_form(request: HttpRequest, slug: str) -> HttpResponse:
    """The parameter form for one model, built from its published schema.

    Fetched when the user picks a model, so no schema is downloaded until it is
    actually needed.
    """
    spec = catalog.spec(slug)
    if spec is None:
        return _fragment(
            html_service.error_fragment("That model is no longer listed in the catalogue."), status=404
        )

    html = render_to_string(
        "partials/model_form.html",
        {"spec": spec, "category": spec.category},
        request=request,
    )
    return _fragment(html)


@login_required
@require_POST
def create(request: HttpRequest, slug: str) -> HttpResponse:
    """Submit a generation and return the card that tracks it."""
    spec = catalog.spec(slug)
    if spec is None:
        return _fragment(
            html_service.error_fragment("That model is no longer listed in the catalogue."), status=404
        )

    preview_input, _ = inputs.build_input(spec, request.POST, [])
    missing = inputs.missing_required(spec, preview_input)
    if missing and not request.FILES.getlist("reference_images"):
        return _fragment(
            html_service.error_fragment("Fill in: " + ", ".join(missing)), status=400
        )

    try:
        generation = engine.start(
            user=request.user,
            spec=spec,
            form_data=request.POST,
            files=request.FILES.getlist("reference_images"),
        )
    except kie.MissingApiKey as exc:
        return _fragment(html_service.error_fragment(exc.message), status=400)
    except kie.KieError as exc:
        return _fragment(html_service.error_fragment(exc.message), status=502)

    user_settings = request.user.settings
    if user_settings.default_model_for(spec.category) != spec.slug:
        user_settings.set_default_model_for(spec.category, spec.slug)
        user_settings.save(update_fields=[f"default_{spec.category}_model", "updated_at"])

    return _fragment(engine.render(generation))


@login_required
def status(request: HttpRequest, pk: int) -> HttpResponse:
    """Poll one generation and return its card, refreshed."""
    generation = get_object_or_404(
        Generation.objects.prefetch_related("assets"), pk=pk, user=request.user
    )
    generation = engine.refresh(generation)

    response = _fragment(engine.render(generation))
    # `status` is a TextChoices member: a str subclass, which WSGI rejects as a
    # header value, so hand it over as a plain string.
    response["X-Generation-Status"] = str(generation.status)
    return response


@login_required
def gallery(request: HttpRequest) -> HttpResponse:
    """Everything the user has generated, newest first."""
    generations = Generation.objects.filter(user=request.user).prefetch_related("assets")

    category = request.GET.get("category")
    if category in STUDIO_CATEGORIES:
        generations = generations.filter(category=category)

    page = Paginator(generations, 24).get_page(request.GET.get("page"))
    return render(
        request,
        "pages/gallery.html",
        {"generations": page, "category": "gallery", "active_filter": category or ""},
    )

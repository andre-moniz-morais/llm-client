"""API access to generations."""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.catalog.services import catalog
from apps.common.services import kie
from apps.generation.filters import GenerationFilter
from apps.generation.models import Generation
from apps.generation.serializers import GenerationSerializer
from apps.generation.services import engine, inputs


class GenerationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Generations belonging to the signed-in user.

    Creation goes through :meth:`create` rather than a serializer, because the
    accepted parameters are whatever the chosen model publishes.
    """

    serializer_class = GenerationSerializer
    filterset_class = GenerationFilter
    search_fields = ["prompt", "model_name"]
    ordering_fields = ["created_at", "completed_at", "status"]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_queryset(self):
        return Generation.objects.filter(user=self.request.user).prefetch_related("assets")

    def create(self, request):
        slug = request.data.get("model") or ""
        spec = catalog.spec(slug)
        if spec is None:
            raise ValidationError({"model": "No such model in the catalog."})
        if spec.category not in {"image", "video", "music"}:
            raise ValidationError({"model": f"{spec.name} is not a generation model."})

        files = request.FILES.getlist("reference_images") if hasattr(request, "FILES") else []
        preview, _ = inputs.build_input(spec, request.data, [])
        missing = inputs.missing_required(spec, preview)
        if missing and not files:
            raise ValidationError({"input": f"Missing required fields: {', '.join(missing)}"})

        try:
            generation = engine.start(
                user=request.user, spec=spec, form_data=request.data, files=files
            )
        except kie.MissingApiKey as exc:
            return Response({"detail": exc.message}, status=400)
        except kie.KieError as exc:
            return Response({"detail": exc.message}, status=502)

        return Response(self.get_serializer(generation).data, status=201)

    @action(detail=True, methods=["get", "post"])
    def refresh(self, request, pk=None):
        """Poll KIE for this task's current state."""
        generation = engine.refresh(self.get_object())
        return Response(self.get_serializer(generation).data)

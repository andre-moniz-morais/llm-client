"""Read-only API over the model catalog."""

from __future__ import annotations

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from apps.catalog.filters.model import filter_models
from apps.catalog.serializers import ModelRefSerializer, ModelSpecSerializer
from apps.catalog.services import catalog


class ModelViewSet(viewsets.ViewSet):
    """Lists the models KIE.ai publishes, and one model's request schema.

    Listing reads the documentation index (one request, cached); retrieving a
    model additionally reads that model's page, which is why the two are
    separate endpoints rather than one fat payload.
    """

    lookup_value_regex = r"[-\w.]+"

    def list(self, request):
        refresh = request.query_params.get("refresh") == "1"
        models = filter_models(catalog.index(refresh=refresh), request.query_params)
        return Response(ModelRefSerializer([model.as_dict() for model in models], many=True).data)

    def retrieve(self, request, pk: str):
        spec = catalog.spec(pk, refresh=request.query_params.get("refresh") == "1")
        if spec is None:
            raise NotFound("No such model.")
        return Response(ModelSpecSerializer(spec.as_dict()).data)

    @action(detail=False, methods=["get"])
    def categories(self, request):
        counts: dict[str, int] = {}
        for model in catalog.index():
            counts[model.category] = counts.get(model.category, 0) + 1
        return Response(
            [{**category, "count": counts.get(category["key"], 0)} for category in catalog.categories()]
        )

    @action(detail=False, methods=["get"])
    def providers(self, request):
        category = request.query_params.get("category")
        if category:
            return Response(catalog.providers_for(category))
        return Response(sorted({model.provider for model in catalog.index() if model.provider}))

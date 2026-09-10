"""Query filters for the generation API."""

from __future__ import annotations

import django_filters as filters

from apps.generation.models import Asset, Generation


class GenerationFilter(filters.FilterSet):
    prompt = filters.CharFilter(lookup_expr="icontains")
    provider = filters.CharFilter(field_name="model_name", lookup_expr="icontains")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    pending = filters.BooleanFilter(method="filter_pending", label="Still running")

    class Meta:
        model = Generation
        fields = ["category", "status", "model_slug", "adapter"]

    def filter_pending(self, queryset, name, value):
        running = [Generation.Status.PENDING, Generation.Status.RUNNING]
        return queryset.filter(status__in=running) if value else queryset.exclude(status__in=running)


class AssetFilter(filters.FilterSet):
    class Meta:
        model = Asset
        fields = ["kind", "generation"]

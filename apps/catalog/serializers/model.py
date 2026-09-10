"""Serializers for the model catalog.

The catalog is not a database table - it is read from the KIE documentation -
so these are plain serializers over the parsed dataclasses.
"""

from __future__ import annotations

from rest_framework import serializers


class ModelRefSerializer(serializers.Serializer):
    """A model as listed, without its request schema."""

    slug = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField()
    provider = serializers.CharField()
    doc_url = serializers.URLField(allow_blank=True)
    summary = serializers.CharField(allow_blank=True)


class ModelFieldSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    enum = serializers.ListField(child=serializers.JSONField(), required=False)
    default = serializers.JSONField(required=False, allow_null=True)
    required = serializers.BooleanField(default=False)
    minimum = serializers.JSONField(required=False, allow_null=True)
    maximum = serializers.JSONField(required=False, allow_null=True)
    max_items = serializers.IntegerField(required=False, allow_null=True)
    example = serializers.JSONField(required=False, allow_null=True)


class ModelSpecSerializer(ModelRefSerializer):
    """A model with everything needed to build a request for it."""

    summary = serializers.CharField(source="description", allow_blank=True)
    model_id = serializers.CharField()
    adapter = serializers.CharField()
    create_path = serializers.CharField()
    prompt_field = serializers.CharField(allow_blank=True)
    image_field = serializers.CharField(allow_blank=True)
    fields = ModelFieldSerializer(many=True)

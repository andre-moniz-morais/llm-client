"""Serializers for generations and their produced files."""

from __future__ import annotations

from rest_framework import serializers

from apps.generation.models import Asset, Generation


class AssetSerializer(serializers.ModelSerializer):
    url = serializers.CharField(read_only=True)

    class Meta:
        model = Asset
        fields = ["id", "kind", "url", "remote_url", "thumbnail_url", "title", "metadata", "created_at"]
        read_only_fields = fields


class GenerationSerializer(serializers.ModelSerializer):
    assets = AssetSerializer(many=True, read_only=True)

    class Meta:
        model = Generation
        fields = [
            "id",
            "category",
            "model_slug",
            "model_id",
            "model_name",
            "adapter",
            "prompt",
            "parameters",
            "reference_images",
            "task_id",
            "status",
            "error",
            "assets",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = fields


class CreateGenerationSerializer(serializers.Serializer):
    """The body of a generation request posted through the API.

    Model parameters are validated against the model's published schema rather
    than declared here, so this only carries what is common to every model.
    """

    model = serializers.CharField(help_text="Catalog slug of the model to run.")
    reference_images = serializers.ListField(
        child=serializers.ImageField(), required=False, default=list
    )

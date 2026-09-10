"""Query filters for the chat API."""

from __future__ import annotations

import django_filters as filters

from apps.chat.models import Conversation, Message


class ConversationFilter(filters.FilterSet):
    model_slug = filters.CharFilter(lookup_expr="iexact")
    title = filters.CharFilter(lookup_expr="icontains")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = Conversation
        fields = ["model_slug", "archived", "title"]


class MessageFilter(filters.FilterSet):
    content = filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = Message
        fields = ["conversation", "role", "status"]

"""Keep a :class:`UserSettings` row alongside every user."""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserSettings


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_settings(sender, instance, created, **kwargs) -> None:
    if created:
        UserSettings.objects.get_or_create(user=instance)

# Loaded with Django so that @shared_task binds to this app's configuration.
from .celery import app as celery_app

__all__ = ["celery_app"]

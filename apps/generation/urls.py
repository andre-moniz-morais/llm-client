from django.urls import path

from apps.generation import views

app_name = "generation"

urlpatterns = [
    path("image/", views.studio, {"category": "image"}, name="image"),
    path("video/", views.studio, {"category": "video"}, name="video"),
    path("music/", views.studio, {"category": "music"}, name="music"),
    path("library/", views.gallery, name="gallery"),
    path("models/<str:slug>/form/", views.model_form, name="model-form"),
    path("models/<str:slug>/create/", views.create, name="create"),
    path("tasks/<int:pk>/status/", views.status, name="status"),
]

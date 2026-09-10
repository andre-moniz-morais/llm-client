from django.urls import path

from apps.chat import views

app_name = "chat"

urlpatterns = [
    path("", views.index, name="index"),
    path("send/", views.send, name="send"),
    path("<int:pk>/", views.conversation_detail, name="detail"),
    path("<int:pk>/archive/", views.archive, name="archive"),
]

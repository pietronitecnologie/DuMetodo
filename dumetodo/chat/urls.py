from django.urls import path

from . import views

app_name = "chat"
urlpatterns = [
    path("", views.pannello, name="pannello"),
    path("stato/", views.stato, name="stato"),
    path("invia/", views.invia, name="invia"),
    path("letti/", views.letti, name="letti"),
    path("chiudi/", views.chiudi, name="chiudi"),
    path("riapri/", views.riapri, name="riapri"),
]

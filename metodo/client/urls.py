from django.urls import path

from . import views

app_name = "client"
urlpatterns = [
    path("configurazione.json", views.configurazione, name="configurazione"),
    path("aggiornamento.json", views.aggiornamento, name="aggiornamento"),
    path("scarica/<int:pk>/", views.scarica, name="scarica"),
    path("versioni/", views.versioni, name="versioni"),
]

from django.urls import path

from . import views

app_name = "configurazione"
urlpatterns = [
    path("", views.pagina, name="pagina"),
    path("storico/", views.storico, name="storico"),
    path("logo.png", views.logo, name="logo"),
    path("casella/prova/", views.prova_casella, name="prova_casella"),
    path("tracciati/anteprima/", views.anteprima_tracciato, name="anteprima_tracciato"),
    path("tracciati/guida/", views.guida_tracciati, name="guida_tracciati"),
]

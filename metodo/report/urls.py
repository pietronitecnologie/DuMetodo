from django.urls import path

from . import views

app_name = "report"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("produttivita/", views.produttivita, name="produttivita"),
    path("produttivita/ore/", views.ore_lavorate, name="ore_lavorate"),
    path("materiale/", views.materiale, name="materiale"),
    path("settimane/", views.settimane, name="settimane"),
    path("prezzi/", views.prezzi, name="prezzi"),
    path("efficienza-reparti/", views.efficienza_reparti, name="efficienza_reparti"),
    path("puntualita/", views.puntualita, name="puntualita"),
    path("ore-lavoratori/", views.ore_lavoratori, name="ore_lavoratori"),
]

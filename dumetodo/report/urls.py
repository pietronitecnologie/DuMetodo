from django.urls import path

from . import views

app_name = "report"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("produttivita/", views.produttivita, name="produttivita"),
    path("produttivita/ore/", views.ore_lavorate, name="ore_lavorate"),
    path("materiale/", views.materiale, name="materiale"),
    path("prezzi/", views.prezzi, name="prezzi"),
]

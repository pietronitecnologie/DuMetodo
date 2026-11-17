from django.urls import path

from . import views

app_name = "configurazione"
urlpatterns = [
    path("", views.pagina, name="pagina"),
    path("storico/", views.storico, name="storico"),
]

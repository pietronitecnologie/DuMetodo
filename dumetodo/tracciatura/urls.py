from django.urls import path

from . import views

app_name = "tracciatura"
urlpatterns = [
    path("", views.elenco, name="elenco"),
    path("nuova/", views.nuova, name="nuova"),
    path("<int:pk>/", views.dettaglio, name="tracciatura"),
    path("file/<int:pk>/", views.file, name="file"),
    path("pannello/", views.pannello, name="pannello"),
    path("modelli/", views.modelli, name="modelli"),
    path("modelli/<int:pk>/", views.modello, name="modello"),
]

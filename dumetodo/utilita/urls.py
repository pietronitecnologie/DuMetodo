from django.urls import path

from . import views

app_name = "utilita"
urlpatterns = [
    path("", views.indice, name="indice"),
    path("annunci/", views.annunci, name="annunci"),
    path("annunci/nuovo/", views.annuncio, name="annuncio_nuovo"),
    path("annunci/<int:pk>/", views.annuncio, name="annuncio"),
    path("annunci/<int:pk>/attiva/", views.annuncio_attiva, name="annuncio_attiva"),
    path("annunci/attivi/", views.annunci_attivi, name="annunci_attivi"),
    path("ristampa-etichette/", views.ristampa_etichette, name="ristampa_etichette"),
    path("scorporazione/", views.scorporazione, name="scorporazione"),
    path("cambia-ordine/", views.cambia_ordine_cerca, name="cambia_ordine_cerca"),
    path("cambia-ordine/<int:pk>/", views.cambia_ordine, name="cambia_ordine"),
]

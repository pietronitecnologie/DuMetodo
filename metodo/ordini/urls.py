from django.urls import path

from . import views

app_name = "ordini"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("articoli/", views.articoli, name="articoli"),
    path("seleziona/", views.seleziona, name="seleziona"),
    path("vai/", views.vai, name="vai"),
    path("<int:pk>/", views.dettaglio, name="dettaglio"),
    path("<int:pk>/marca/", views.marca, name="marca"),
    path("<int:pk>/stato/", views.cambia_stato, name="cambia_stato"),
    path("<int:pk>/scadenza/", views.cambia_scadenza, name="cambia_scadenza"),
    path("<int:pk>/righe/<int:riga_id>/azione/", views.azione_riga, name="azione_riga"),
    path("<int:pk>/righe/<int:riga_id>/dividi/", views.dividi_riga, name="dividi_riga"),
]

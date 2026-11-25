from django.urls import path

from . import views

app_name = "importazione"
urlpatterns = [
    path("", views.pagina, name="pagina"),
    path("leggi-casella/", views.leggi_casella_ora, name="leggi_casella"),
    path("problemi/", views.problemi, name="problemi"),
    path("problemi/<int:pk>/gestito/", views.segna_gestito, name="segna_gestito"),
    path("file/<int:pk>/", views.dettaglio_file, name="dettaglio_file"),
    path("file/<int:pk>/scarica/", views.scarica_file, name="scarica_file"),
    path("file/<int:pk>/rielabora/", views.rielabora, name="rielabora"),
]

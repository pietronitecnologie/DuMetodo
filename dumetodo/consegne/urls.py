from django.urls import path

from . import views

app_name = "consegne"
urlpatterns = [
    path("", views.pagina, name="pagina"),
    path("etichette/", views.etichette, name="etichette"),
    path("consegna/", views.consegna, name="consegna"),
    path("rimuovi/<int:ordine_id>/", views.rimuovi, name="rimuovi"),
    path("storico/", views.storico, name="storico"),
    path("<int:pk>/", views.dettaglio, name="dettaglio"),
    path("<int:pk>/file/", views.file_consegna, name="file"),
    path("ddt/", views.ddt, name="ddt"),
    path("ddt/storico/", views.ddt_storico, name="ddt_storico"),
    path("ddt/<int:pk>/file/", views.ddt_file, name="ddt_file"),
    path("ddt/<int:pk>/reinvia/", views.ddt_reinvia, name="ddt_reinvia"),
]

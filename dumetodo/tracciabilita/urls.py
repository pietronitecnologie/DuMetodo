from django.urls import path

from . import views

app_name = "tracciabilita"
urlpatterns = [
    # tracce generiche e stampa
    path("tracce/", views.tracce, name="tracce"),
    path("tracce/nuova/", views.traccia_nuova, name="traccia_nuova"),
    path("tracce/<int:pk>/", views.traccia, name="traccia"),
    path("stampa/", views.stampa, name="stampa"),
    # ordine: pannello, lotto/busta, file
    path("ordini/<int:ordine_id>/", views.pannello_ordine, name="pannello_ordine"),
    path("ordini/<int:ordine_id>/lotto/", views.lotto_busta, name="lotto_busta"),
    path("ordini/<int:ordine_id>/file/", views.file_ordine_carica, name="file_ordine_carica"),
    path("file/", views.file_lista, name="file_lista"),
    path("file/carica/", views.file_carica, name="file_carica"),
    path("file/<int:pk>/", views.file_scarica, name="file"),
    path("file/<int:pk>/elimina/", views.file_elimina, name="file_elimina"),
    # qualità
    path("qualita/", views.qualita, name="qualita"),
    path("qualita/nuovo/", views.qualita_nuovo, name="qualita_nuovo"),
    path("qualita/controlli/", views.qualita_controlli, name="qualita_controlli"),
]

from django.urls import path

from . import views

app_name = "tracciabilita"
urlpatterns = [
    # batterie
    path("batterie/", views.batterie, name="batterie"),
    path("batterie/nuova/", views.batteria_nuova, name="batteria_nuova"),
    path("batterie/genera-codice/", views.genera_codice, name="genera_codice"),
    # tracce generiche e stampa
    path("tracce/", views.tracce, name="tracce"),
    path("tracce/nuova/", views.traccia_nuova, name="traccia_nuova"),
    path("tracce/<int:pk>/", views.traccia, name="traccia"),
    path("stampa/", views.stampa, name="stampa"),
    # cavi
    path("cavi/", views.cavi, name="cavi"),
    path("cavi/<int:elemento_id>/intestato/", views.cavo_intestato, name="cavo_intestato"),
    path("api/cavi/", views.api_cavi, name="api_cavi"),
    # ordine: pannello, lotto/busta, file
    path("ordini/<int:ordine_id>/", views.pannello_ordine, name="pannello_ordine"),
    path("ordini/<int:ordine_id>/lotto/", views.lotto_busta, name="lotto_busta"),
    path("ordini/<int:ordine_id>/file/", views.file_ordine_carica, name="file_ordine_carica"),
    path("file/", views.file_lista, name="file_lista"),
    path("file/carica/", views.file_carica, name="file_carica"),
    path("file/<int:pk>/", views.file_scarica, name="file"),
    path("file/<int:pk>/elimina/", views.file_elimina, name="file_elimina"),
    # work order batterie
    path("work-order/", views.work_order, name="work_order"),
    path("work-order/genera/", views.work_order_genera, name="work_order_genera"),
    path("work-order/<int:pk>/chiudi/", views.work_order_chiudi, name="work_order_chiudi"),
    # postazioni
    path("postazioni/", views.postazioni, name="postazioni"),
    path("postazioni/<int:pk>/token/", views.postazione_token, name="postazione_token"),
    path("postazioni/<int:pk>/revoca/", views.postazione_revoca, name="postazione_revoca"),
    # qualità
    path("qualita/", views.qualita, name="qualita"),
    path("qualita/nuovo/", views.qualita_nuovo, name="qualita_nuovo"),
    path("qualita/controlli/", views.qualita_controlli, name="qualita_controlli"),
]

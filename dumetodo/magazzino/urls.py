from django.urls import path

from . import views

app_name = "magazzino"
urlpatterns = [
    # anagrafiche
    path("componenti/", views.componenti, name="componenti"),
    path("componenti/nuovo/", views.componente_nuovo, name="componente_nuovo"),
    path("componenti/<int:pk>/", views.componente_modifica, name="componente"),
    path("componenti/suggerimenti/", views.suggerimenti_componenti, name="suggerimenti"),
    path("articoli/", views.articoli, name="articoli"),
    path("articoli/<int:pk>/modifica/", views.articolo_modifica, name="articolo_modifica"),
    path("articoli/<int:pk>/nuovo/", views.articolo_nuovo, name="articolo_nuovo"),
    # inventario
    path("inventario/", views.inventario, name="inventario"),
    path("inventario/aggiungi/", views.inventario_aggiungi, name="inventario_aggiungi"),
    path("inventario/esporta/", views.inventario_esporta, name="inventario_esporta"),
    # richieste materiali
    path("richieste/", views.richieste, name="richieste"),
    path("richieste/<int:pk>/ricevi/", views.richiesta_ricevi, name="richiesta_ricevi"),
    path("richieste/<int:pk>/elimina/", views.richiesta_elimina, name="richiesta_elimina"),
    path("richieste/<int:pk>/urgenza/", views.richiesta_urgenza, name="richiesta_urgenza"),
    path("richieste/reparto/<str:codice>/", views.richieste_reparto, name="richieste_reparto"),
    path("richieste/reparto/<str:codice>/aggiungi/", views.richiesta_aggiungi, name="richiesta_aggiungi"),
    path("richieste/reparto/<str:codice>/esporta/", views.richieste_esporta, name="richieste_esporta"),
    path("richieste/reparto/<str:codice>/email/", views.richieste_anteprima, name="richieste_anteprima"),
    path("richieste/reparto/<str:codice>/email/invia/", views.richieste_invia, name="richieste_invia"),
    # distinte base
    path("distinte/", views.distinte, name="distinte"),
    path("distinte/nuova/", views.distinta_crea, name="distinta_crea"),
    path("distinte/esporta/", views.distinte_esporta, name="distinte_esporta"),
    path("distinte/<int:pk>/", views.distinta, name="distinta"),
    path("distinte/<int:pk>/modifica/", views.distinta_modifica, name="distinta_modifica"),
    path("distinte/<int:pk>/elimina/", views.distinta_elimina, name="distinta_elimina"),
    path("distinte/<int:pk>/componenti/", views.distinta_componente_aggiungi, name="distinta_componente_aggiungi"),
    path("distinte/componenti/<int:pk>/modifica/", views.componente_distinta_modifica, name="riga_distinta_modifica"),
    path("distinte/componenti/<int:pk>/elimina/", views.componente_distinta_elimina, name="riga_distinta_elimina"),
    path("distinte/componenti/<int:pk>/sposta/", views.componente_distinta_sposta, name="riga_distinta_sposta"),
    # DDT in ingresso
    path("ddt/", views.ddt_lista, name="ddt_lista"),
    path("ddt/nuovo/", views.ddt_crea, name="ddt_crea"),
    path("ddt/importa/", views.ddt_importa, name="ddt_importa"),
    path("ddt/materiali/", views.materiali, name="materiali"),
    path("ddt/<int:pk>/", views.ddt, name="ddt"),
    path("ddt/<int:pk>/materiali/", views.ddt_materiale_aggiungi, name="ddt_materiale_aggiungi"),
    path("ddt/<int:pk>/verifica/", views.ddt_verifica, name="ddt_verifica"),
    path("ddt/materiali/<int:pk>/elimina/", views.ddt_materiale_elimina, name="ddt_materiale_elimina"),
]

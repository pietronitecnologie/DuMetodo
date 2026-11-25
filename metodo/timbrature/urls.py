from django.urls import path

from . import views

app_name = "timbrature"
urlpatterns = [
    path("", views.presenze, name="presenze"),
    path("elenco/", views.timbrature, name="timbrature"),
    path("lavoratori/", views.lavoratori, name="lavoratori"),
    path("lavoratori/nuovo/", views.lavoratore, name="lavoratore_nuovo"),
    path("lavoratori/<int:pk>/", views.lavoratore, name="lavoratore"),
    path("postazioni/", views.postazioni, name="postazioni"),
    path("badge/<int:pk>/", views.badge, name="badge"),
    path("costi/", views.costi, name="costi"),
    # Pagina della postazione, senza login: il link segreto identifica la postazione.
    path("p/<str:token>/", views.postazione, name="postazione"),
    path("timbra/", views.timbra_qui, name="timbra_qui"),
    path("p/<str:token>/identifica/", views.postazione_identifica, name="postazione_identifica"),
    path("p/<str:token>/timbra/", views.postazione_timbra, name="postazione_timbra"),
]

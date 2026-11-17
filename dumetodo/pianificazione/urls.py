from django.urls import path

from . import views

app_name = "pianificazione"
urlpatterns = [
    path("", views.pagina, name="pagina"),
    path("soglie/", views.soglie, name="soglie"),
    path("export/", views.export, name="export"),
    path("report/", views.invia_report, name="invia_report"),
    path("agenda/", views.agenda, name="agenda"),
    path("agenda/pulsanti/", views.agenda_pulsanti, name="agenda_pulsanti"),
    path("tabulati/settimanale/", views.tabulato_settimanale, name="tabulato_settimanale"),
    path("tabulati/scorte/", views.tabulato_scorte, name="tabulato_scorte"),
    path("tabulati/commesse/", views.tabulato_commesse, name="tabulato_commesse"),
]

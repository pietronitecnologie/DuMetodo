from django.urls import path

from . import views

app_name = "ticket"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuovo/", views.nuovo, name="nuovo"),
    path("<int:pk>/", views.dettaglio, name="dettaglio"),
    path("<int:pk>/azione/", views.azione, name="azione"),
    path("ordine/<int:pk>/", views.pannello_ordine, name="pannello_ordine"),
]

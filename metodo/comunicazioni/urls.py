from django.urls import path

from . import views

app_name = "note"
urlpatterns = [
    path("ordine/<int:ordine_id>/", views.pannello, name="pannello"),
    path("ordine/<int:ordine_id>/aggiungi/", views.aggiungi, name="aggiungi"),
    path("<int:nota_id>/elimina/", views.elimina, name="elimina"),
]

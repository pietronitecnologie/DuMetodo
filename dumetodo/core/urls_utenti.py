from django.urls import path

from . import views_utenti as views

app_name = "utenti"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuovo/", views.modifica, name="nuovo"),
    path("<int:pk>/", views.modifica, name="modifica"),
]

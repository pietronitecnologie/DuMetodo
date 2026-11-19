from django.urls import path

from . import views_reparti as views

app_name = "reparti"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuovo/", views.modifica, name="nuovo"),
    path("<int:pk>/", views.modifica, name="modifica"),
]

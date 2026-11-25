from django.urls import path

from . import views

app_name = "committenti"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuovo/", views.modifica, name="nuovo"),
    path("<int:pk>/", views.modifica, name="modifica"),
    path("scegli/", views.scegli, name="scegli"),
]

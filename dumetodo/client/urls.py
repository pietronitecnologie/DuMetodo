from django.urls import path

from . import views

app_name = "client"
urlpatterns = [path("configurazione.json", views.configurazione, name="configurazione")]

from django.urls import path

from . import views

app_name = "tracciabilita"
urlpatterns = [
    # Archivio delle tracce (sola lettura)
    path("tracce/", views.tracce, name="tracce"),
    path("tracce/<int:pk>/", views.traccia, name="traccia"),
    path("stampa/", views.stampa, name="stampa"),
    path("ordini/<int:ordine_id>/", views.pannello_ordine, name="pannello_ordine"),
]

from django.urls import path

from . import views

app_name = "gestionale"
urlpatterns = [
    path("", views.indice, name="indice"),
    path("ddt/emetti/", views.ddt_emetti, name="ddt_emetti"),
    path("ddt/", views.ddt_lista, name="ddt_lista"),
    path("ddt/<int:pk>/", views.ddt, name="ddt"),
    path("ddt/<int:pk>/stampa/", views.ddt_stampa, name="ddt_stampa"),
    path("da-fatturare/", views.da_fatturare, name="da_fatturare"),
    path("fatture/", views.fatture, name="fatture"),
    path("fatture/<int:pk>/", views.fattura, name="fattura"),
    path("fatture/<int:pk>/xml/", views.fattura_xml, name="fattura_xml"),
    path("fatture/<int:pk>/stampa/", views.fattura_stampa, name="fattura_stampa"),
    path("impostazioni/", views.impostazioni, name="impostazioni"),
    path("numerazioni/", views.numerazioni, name="numerazioni"),
]

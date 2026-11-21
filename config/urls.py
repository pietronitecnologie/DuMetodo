from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from dumetodo.core import views as core_views


class FaviconView(RedirectView):
    permanent = True

    def get_redirect_url(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return static("img/favicon.ico")  # calcolato alla richiesta: il manifest dei file statici esiste già


urlpatterns = [
    path("", core_views.home, name="home"),
    # I browser chiedono /favicon.ico anche senza <link>: si rimanda all'icona statica.
    path("favicon.ico", FaviconView.as_view()),
    path("manuale/", core_views.manuale, name="manuale"),
    path("manuale/manuale-utente.md", core_views.manuale_md, name="manuale_md"),
    path("accesso/", auth_views.LoginView.as_view(), name="login"),
    path("uscita/", auth_views.LogoutView.as_view(), name="logout"),
    path("ordini/", include("dumetodo.ordini.urls")),
    path("consegne/", include("dumetodo.consegne.urls")),
    path("pianificazione/", include("dumetodo.pianificazione.urls")),
    path("report/", include("dumetodo.report.urls")),
    path("utilita/", include("dumetodo.utilita.urls")),
    path("note/", include("dumetodo.comunicazioni.urls")),
    path("importazione/", include("dumetodo.importazione.urls")),
    path("magazzino/", include("dumetodo.magazzino.urls")),
    path("configurazione/", include("dumetodo.configurazione.urls")),
    path("utenti/", include("dumetodo.core.urls_utenti")),
    path("reparti/", include("dumetodo.core.urls_reparti")),
    path("chat/", include("dumetodo.chat.urls")),
    path("tracciabilita/", include("dumetodo.tracciabilita.urls")),
    path("client/", include("dumetodo.client.urls")),
    path("gestionale/", include("dumetodo.gestionale.urls")),
    path("timbrature/", include("dumetodo.timbrature.urls")),
    path("tracciatura/", include("dumetodo.tracciatura.urls")),
    path("admin/", admin.site.urls),
]

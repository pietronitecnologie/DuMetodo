from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from metodo.core import views as core_views


class FaviconView(RedirectView):
    permanent = True

    def get_redirect_url(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return static("img/favicon.ico")  # calcolato alla richiesta: il manifest dei file statici esiste già


from metodo.core import cronologia  # noqa: E402

urlpatterns = [
    path("", core_views.home, name="home"),
    path("indietro/", cronologia.indietro, name="indietro"),
    # I browser chiedono /favicon.ico anche senza <link>: si rimanda all'icona statica.
    path("favicon.ico", FaviconView.as_view()),
    path("manuale/", core_views.manuale, name="manuale"),
    path("manuale/manuale-utente.md", core_views.manuale_md, name="manuale_md"),
    path("accesso/", auth_views.LoginView.as_view(), name="login"),
    path("uscita/", auth_views.LogoutView.as_view(), name="logout"),
    path("ordini/", include("metodo.ordini.urls")),
    path("consegne/", include("metodo.consegne.urls")),
    path("pianificazione/", include("metodo.pianificazione.urls")),
    path("report/", include("metodo.report.urls")),
    path("utilita/", include("metodo.utilita.urls")),
    path("note/", include("metodo.comunicazioni.urls")),
    path("importazione/", include("metodo.importazione.urls")),
    path("magazzino/", include("metodo.magazzino.urls")),
    path("configurazione/", include("metodo.configurazione.urls")),
    path("utenti/", include("metodo.core.urls_utenti")),
    path("reparti/", include("metodo.core.urls_reparti")),
    path("chat/", include("metodo.chat.urls")),
    path("client/", include("metodo.client.urls")),
    path("gestionale/", include("metodo.gestionale.urls")),
    path("timbrature/", include("metodo.timbrature.urls")),
    path("tracciatura/", include("metodo.tracciatura.urls")),
    path("admin/", admin.site.urls),
]

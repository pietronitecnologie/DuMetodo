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
    path("tracciabilita/", include("dumetodo.tracciabilita.urls")),
    path("client/", include("dumetodo.client.urls")),
    path("admin/", admin.site.urls),
]

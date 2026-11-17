from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from dumetodo.core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
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
    path("tracciabilita/", include("dumetodo.tracciabilita.urls")),
    path("client/", include("dumetodo.client.urls")),
    path("admin/", admin.site.urls),
]

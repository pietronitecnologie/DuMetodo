from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="ordini:lista", permanent=False)),
    path("accesso/", auth_views.LoginView.as_view(), name="login"),
    path("uscita/", auth_views.LogoutView.as_view(), name="logout"),
    path("ordini/", include("dumetodo.ordini.urls")),
    path("consegne/", include("dumetodo.consegne.urls")),
    path("pianificazione/", include("dumetodo.pianificazione.urls")),
    path("report/", include("dumetodo.report.urls")),
    path("utilita/", include("dumetodo.utilita.urls")),
    path("note/", include("dumetodo.comunicazioni.urls")),
    path("importazione/", include("dumetodo.importazione.urls")),
    path("configurazione/", include("dumetodo.configurazione.urls")),
    path("utenti/", include("dumetodo.core.urls_utenti")),
    path("admin/", admin.site.urls),
]

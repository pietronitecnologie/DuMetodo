from django.apps import AppConfig


class ConfigurazioneConfig(AppConfig):
    name = "dumetodo.configurazione"
    verbose_name = "Configurazione"

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registra i system check)

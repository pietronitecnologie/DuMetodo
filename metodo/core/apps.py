from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "metodo.core"
    verbose_name = "Base"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        from .permessi import sincronizza_permessi

        post_migrate.connect(sincronizza_permessi, sender=self)

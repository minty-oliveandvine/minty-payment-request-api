from django.apps import AppConfig
from django.conf import settings


class SharedModelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shared_models"
    verbose_name = "Shared Models (Module 1)"

    def ready(self):
        # Every table is the schema's (docs/schema/01_schema_rebased.sql in the Minty repo):
        # all models are ``managed = False`` and this service ships no DDL (``bills/migrations``
        # went in C8). On SQLite - the test path with no schema file - the tables are built
        # FROM the models instead, so ``managed`` is flipped on for every model of both apps.
        if getattr(settings, "SHARED_MODELS_MANAGED_FOR_TESTING", False):
            from django.apps import apps

            for app_label in ("shared_models", "bills"):
                for model in apps.get_app_config(app_label).get_models():
                    model._meta.managed = True

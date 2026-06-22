from django.apps import AppConfig
from django.conf import settings


class SharedModelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shared_models"
    verbose_name = "Shared Models (Module 1)"

    def ready(self):
        if getattr(settings, "SHARED_MODELS_MANAGED_FOR_TESTING", False):
            from shared_models import models

            for model in (
                models.User,
                models.Entity,
                models.UserEntity,
                models.AccountInfo,
                models.XeroContactSync,
            ):
                model._meta.managed = True

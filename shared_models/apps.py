from django.apps import AppConfig
from django.conf import settings


class SharedModelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shared_models"
    verbose_name = "Shared Models (Module 1)"

    def ready(self):
        if getattr(settings, "SHARED_MODELS_MANAGED_FOR_TESTING", False):
            from shared_models import models

            from bills import models as bill_models

            for model in (
                bill_models.CurrencyInfo,
                models.User,
                models.UserToken,
                models.Entity,
                models.CountryInfo,
                models.UserEntity,
                models.EntityModuleSubscription,
                models.AccountInfo,
                models.XeroContactSync,
            ):
                model._meta.managed = True

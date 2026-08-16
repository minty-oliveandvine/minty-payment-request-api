from django.db import models


class User(models.Model):
    """Read-only mirror of pettycashv2.user managed by the Flask app."""

    id = models.CharField(max_length=36, primary_key=True)
    email = models.CharField(max_length=100, unique=True)
    password = models.CharField(max_length=150)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    username = models.CharField(max_length=150, unique=True)
    system_role = models.CharField(max_length=20, default="normal")
    approved = models.BooleanField(default=False)
    access_token = models.CharField(max_length=2048, null=True, blank=True)
    refresh_token = models.CharField(max_length=255, null=True, blank=True)
    id_token = models.CharField(max_length=2048, null=True, blank=True)
    expires_in = models.IntegerField(null=True, blank=True)
    token_created_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    xero_entity_id = models.CharField(max_length=36, null=True, blank=True)
    # Sign-in presence behind Minty's Settings > Users list. Owned by the Flask
    # app (services/user_presence.py, migration p1a01_user_presence) — billing
    # only ever clears signed_in_at, on logout, so signing out of the billing
    # profile takes you off that list the same way signing out of Minty does.
    signed_in_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "user"

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"


class Entity(models.Model):
    """Read-only mirror of pettycashv2.entities managed by the Flask app."""

    id = models.CharField(max_length=36, primary_key=True)
    name = models.CharField(max_length=100)
    # FKs into the registries: country_code is the ISO alpha-2
    # country_info PK; currency_id is a uuid into currency_info(id)
    # (Alembic c8e0a2b4d6f8 / d0f2b4c6e8a0 reshaped both).
    country_code = models.CharField(max_length=2, null=True, blank=True)
    currency_id = models.UUIDField(null=True, blank=True)
    xero_org_id = models.CharField(max_length=36, null=True, blank=True)
    xero_short_code = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=20, default="active")
    timezone = models.CharField(max_length=30, null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    period_lock_date = models.DateField(null=True, blank=True)
    end_of_year_lock_date = models.DateField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "entities"

    def __str__(self):
        return self.name


class UserEntity(models.Model):
    """Read-only mirror of pettycashv2.user_entity managed by the Flask app."""

    user = models.OneToOneField(
        User,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        primary_key=True,
    )
    entity = models.ForeignKey(
        Entity, on_delete=models.DO_NOTHING, db_column="entity_id"
    )
    role = models.CharField(max_length=20)
    approved = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = "user_entity"
        unique_together = ("user", "entity")


class EntityModuleSubscription(models.Model):
    """Read-only mirror of pettycashv2.entity_module_subscription, owned by Flask.

    Mirrored here for one field: ``payer_user_id``, the person whose card this
    company's billing sits on. Signing yourself out of a company has to refuse
    while you are that person, and this is the only place that fact is recorded.

    One row per module, and one payer per entity across them all (Flask enforces
    that on write), so any row answers "who pays for this company".
    """

    id = models.CharField(max_length=36, primary_key=True)
    entity_id = models.CharField(max_length=36, db_index=True)
    function_code = models.CharField(max_length=100)
    payer_user_id = models.CharField(max_length=36, db_index=True)
    phase = models.CharField(max_length=30)

    class Meta:
        managed = False
        db_table = "entity_module_subscription"

    def __str__(self):
        return f"{self.entity_id}/{self.function_code} paid by {self.payer_user_id}"


class AccountInfo(models.Model):
    """Mirror of pettycashv2.account_info managed by the Flask app.

    The status field is written by Module 2 (Django) when the user toggles
    account codes in Bill Settings, keeping Module 1 in sync.  All other
    structural changes (insert/delete/schema) remain Flask's responsibility.
    """

    id = models.CharField(max_length=36, primary_key=True)
    entity_id = models.CharField(max_length=36)
    type = models.CharField(max_length=50)
    name = models.CharField(max_length=80)
    xero_account_id = models.CharField(max_length=36, null=True, blank=True)
    xero_code = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=50, default="ACTIVE")
    class_type = models.CharField(max_length=50, null=True, blank=True)
    description = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "account_info"


class XeroContactSync(models.Model):
    """Read-only mirror of pettycashv2.xero_contact_sync."""

    id = models.CharField(max_length=36, primary_key=True)
    entity_id = models.CharField(max_length=36)
    xero_contact_id = models.CharField(max_length=36)
    xero_org_id = models.CharField(max_length=36, null=True, blank=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "xero_contact_sync"

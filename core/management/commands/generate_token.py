"""
Generate a non-expiring JWT token for API access.

Usage:
    # Auto-pick first user/entity (or create dev records if DB is empty):
    python manage.py generate_token

    # Specify a user and entity:
    python manage.py generate_token --user_id <ID> --entity_id <ID>

    # List available users and entities:
    python manage.py generate_token --list
"""

import uuid

import jwt
from django.conf import settings
from django.core.management.base import BaseCommand

from shared_models.models import Entity, User, UserEntity


class Command(BaseCommand):
    help = "Generate a non-expiring JWT bearer token for API access"

    def add_arguments(self, parser):
        parser.add_argument("--user_id", type=str, help="User ID to generate token for")
        parser.add_argument(
            "--entity_id", type=str, help="Entity ID to embed in the token"
        )
        parser.add_argument(
            "--list", action="store_true", help="List available users and entities"
        )

    def handle(self, *args, **options):
        if options["list"]:
            self._list_available()
            return

        user_id = options["user_id"]
        entity_id = options["entity_id"]

        if user_id and entity_id:
            user, entity, ue = self._resolve(user_id, entity_id)
            if not user or not entity:
                return
        else:
            user, entity, ue = self._auto_resolve()

        payload = {"user_id": user.id, "entity_id": entity.id}
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Non-expiring API token generated"))
        self.stdout.write(f"  User:   {user} (id={user.id})")
        self.stdout.write(f"  Entity: {entity.name} (id={entity.id})")
        if ue:
            self.stdout.write(f"  Role:   {ue.role}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Token:"))
        self.stdout.write(token)
        self.stdout.write("")
        self.stdout.write("Usage:")
        self.stdout.write(f'  curl -H "Authorization: Bearer {token}" \\')
        self.stdout.write(f'       -H "X-Entity-Id: {entity.id}" \\')
        self.stdout.write("       http://localhost:8000/api/v1/bills/")
        self.stdout.write("")

    def _resolve(self, user_id, entity_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"User '{user_id}' not found."))
            return None, None, None

        try:
            entity = Entity.objects.get(id=entity_id)
        except Entity.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Entity '{entity_id}' not found."))
            return None, None, None

        ue = UserEntity.objects.filter(user_id=user_id, entity_id=entity_id).first()
        if not ue:
            self.stderr.write(
                self.style.WARNING(
                    "Warning: User has no role for this entity. "
                    "Token will be generated but API calls will be rejected."
                )
            )
        return user, entity, ue

    def _auto_resolve(self):
        """Pick the first user-entity mapping, or create dev records if empty."""
        ue = (
            UserEntity.objects.select_related("user", "entity")
            .order_by("user_id")
            .first()
        )
        if ue:
            self.stdout.write(f"Auto-selected user={ue.user_id}, entity={ue.entity_id}")
            return ue.user, ue.entity, ue

        self.stdout.write(
            self.style.WARNING("No users found — creating dev records...")
        )

        dev_user_id = str(uuid.uuid4())
        dev_entity_id = str(uuid.uuid4())

        user = User.objects.create(
            id=dev_user_id,
            email="dev@minty.local",
            password="not-a-real-password",
            first_name="Dev",
            last_name="User",
            username="devuser",
            system_role="admin",
            approved=True,
        )
        # entities.country_code / currency_id are FKs into the registries —
        # confirm HK exists and resolve HKD to its uuid (None when the
        # registries are empty).
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                f"SELECT country_code FROM {settings.DB_SCHEMA}.country_info "
                "WHERE country_code = %s",
                ["HK"],
            )
            row = cur.fetchone()
            hk_country_code = row[0] if row else None
            cur.execute(
                f"SELECT id FROM {settings.DB_SCHEMA}.currency_info " "WHERE currency_code = %s",
                ["HKD"],
            )
            row = cur.fetchone()
            hkd_currency_id = row[0] if row else None

        entity = Entity.objects.create(
            id=dev_entity_id,
            name="Dev Entity",
            country_code=hk_country_code,
            currency_id=hkd_currency_id,
            status="active",
        )
        ue = UserEntity.objects.create(user=user, entity=entity, role="admin")

        self.stdout.write(self.style.SUCCESS("Created dev user, entity, and mapping."))
        return user, entity, ue

    def _list_available(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nAvailable Users:"))
        for u in User.objects.all()[:20]:
            self.stdout.write(f"  {u.id}  {u.email}  ({u.first_name} {u.last_name})")

        self.stdout.write(self.style.MIGRATE_HEADING("\nAvailable Entities:"))
        for e in Entity.objects.all()[:20]:
            self.stdout.write(f"  {e.id}  {e.name}")

        self.stdout.write(self.style.MIGRATE_HEADING("\nUser-Entity Mappings:"))
        for ue in UserEntity.objects.select_related("user", "entity").all()[:20]:
            self.stdout.write(
                f"  user={ue.user_id}  entity={ue.entity_id}  role={ue.role}"
            )
        self.stdout.write("")

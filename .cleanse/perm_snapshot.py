"""Exhaustively snapshot permission-checker behaviour: outcome + exact message."""
import json, sys, os, django
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings_test')
django.setup()
from core import permissions as p
from core.exceptions import PermissionDeniedError

ROLES = ["cashier","shop_manager","accountant","admin","super_admin",
         "Admin"," ADMIN ","super-admin","super admin","","viewer",None,"SuperUser"]
STATUSES = ["draft","submitted","paid","partially_paid","voided","approved","", None]

out = {}
for fn in ("check_create_bill","check_mark_paid","check_publish_xero",
           "check_return_bill","check_edit_bill_settings"):
    f = getattr(p, fn)
    for r in ROLES:
        try:
            f(r); res = "OK"
        except PermissionDeniedError as e: res = f"DENY::{e}"
        except Exception as e: res = f"{type(e).__name__}::{e}"
        out[f"{fn}|{r!r}"] = res
for fn in ("check_edit_bill","check_delete_bill"):
    f = getattr(p, fn)
    for r in ROLES:
        for s in STATUSES:
            try:
                f(r, s); res = "OK"
            except PermissionDeniedError as e: res = f"DENY::{e}"
            except Exception as e: res = f"{type(e).__name__}::{e}"
            out[f"{fn}|{r!r}|{s!r}"] = res
for s in STATUSES:
    try:
        p.check_bill_mutable(s); res = "OK"
    except PermissionDeniedError as e: res = f"DENY::{e}"
    except Exception as e: res = f"{type(e).__name__}::{e}"
    out[f"check_bill_mutable|{s!r}"] = res
for r in ROLES:
    out[f"normalize_role|{r!r}"] = repr(p.normalize_role(r))
json.dump(out, open(sys.argv[1],"w"), indent=1, sort_keys=True)
print(f"{len(out)} cases -> {sys.argv[1]}")

"""Snapshot attachment_service behaviour: validation outcomes, s3 keys, log lines, audits."""
import json, sys, os, io, django, logging
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings_test'); django.setup()
from unittest.mock import patch, MagicMock
from django.core.files.uploadedfile import SimpleUploadedFile
from bills.services import attachment_service as A
from core.exceptions import BillValidationError

out = {}
# 1. _resolve_content_type across many inputs
cases = [("a.jpg","image/jpeg"),("a.jpg",""),("a.HEIC","application/octet-stream"),
         ("a.xlsm",""),("a.exe","application/x-msdownload"),("noext","application/pdf"),
         ("a.PDF",None),("a.csv","text/csv"),("weird.svg",""),("a.htm","")]
for name, ct in cases:
    f = SimpleUploadedFile(name, b"x", content_type=ct)
    out[f"resolve|{name}|{ct}"] = repr(A._resolve_content_type(f))
# name=None / empty handled via a stub, Django rejects empty names on SimpleUploadedFile
class _Stub:
    def __init__(self, n, c): self.name=n; self.content_type=c
for n, c in ((None,"application/pdf"), ("", ""), (None, None), ("x.jpg", None)):
    out[f"resolve|stub|{n}|{c}"] = repr(A._resolve_content_type(_Stub(n,c)))

# 2. capture log records + s3 keys + audit calls for both upload paths
class Cap(logging.Handler):
    def __init__(self): super().__init__(); self.recs=[]
    def emit(self, r): self.recs.append(r.getMessage())

def run_upload(kind, role="other", size_ok=True, ctype="application/pdf", fname="inv.pdf"):
    cap = Cap(); lg = logging.getLogger("minty-api"); lg.addHandler(cap); lg.setLevel(logging.INFO)
    keys = []
    s3 = MagicMock()
    s3.upload_fileobj.side_effect = lambda b,bucket,key,**kw: keys.append(key)
    res = {}
    try:
        with patch.object(A, "_get_s3_client", return_value=s3), \
             patch.object(A, "downsize_bytes", side_effect=lambda raw,ct: raw), \
             patch.object(A, "log_audit") as la, \
             patch.object(A, "transaction") as tx, \
             patch.object(A, "Attachment") as At, \
             patch.object(A, "BillAttachment") as BA, \
             patch.object(A, "PaymentAttachment") as PA:
            tx.atomic.return_value.__enter__ = lambda s: None
            tx.atomic.return_value.__exit__ = lambda s,*a: None
            PA.AttachmentRole = __import__('bills.models',fromlist=['x']).PaymentAttachment.AttachmentRole
            At.objects.create.return_value = MagicMock(file_path="p")
            data = b"x" * (5 if size_ok else A.MAX_FILE_SIZE + 1)
            f = SimpleUploadedFile(fname, data, content_type=ctype)
            f.size = len(data)
            bill = MagicMock(entity_id="E1", id="B1")
            pay  = MagicMock(bill_id="B1", id="P1")
            if kind == "bill":
                A.upload_attachment(bill, f, "U1", role)
            else:
                A.upload_payment_attachment(pay, f, "U1", role)
            res["audit_calls"] = [str(c) for c in la.call_args_list]
    except BillValidationError as e:
        res["error"] = str(e)
    except Exception as e:
        res["exc"] = f"{type(e).__name__}: {e}"
    finally:
        lg.removeHandler(cap)
    res["s3_keys"] = [k.split("/",1)[0]+"/..."+k.split("/")[-1][-4:] if k else k for k in keys]
    res["s3_key_shape"] = ["/".join(k.split("/")[:-1]) for k in keys]
    res["logs"] = cap.recs
    return res

for kind in ("bill","payment"):
    for role in ("other","bank_slip","invoice","bogus_role"):
        out[f"upload|{kind}|role={role}"] = run_upload(kind, role)
    out[f"upload|{kind}|badtype"] = run_upload(kind, ctype="application/x-msdownload", fname="a.exe")
    out[f"upload|{kind}|toobig"] = run_upload(kind, size_ok=False)
json.dump(out, open(sys.argv[1],"w"), indent=1, sort_keys=True, default=str)
print(f"{len(out)} cases -> {sys.argv[1]}")

import json, sys, os, django, logging
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings_test'); django.setup()
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError
from bills.services import attachment_service as A
from core.exceptions import BillValidationError

class Cap(logging.Handler):
    def __init__(self): super().__init__(); self.recs=[]
    def emit(self,r): self.recs.append(f"{r.levelname}:{r.getMessage()}")

def run(kind, missing=False, s3_fails=False, orphan=True, xero_id="XA1"):
    cap=Cap(); lg=logging.getLogger("minty-api"); lg.addHandler(cap); lg.setLevel(logging.INFO)
    res={}; order=[]
    try:
        att=MagicMock(file_path="k/f.pdf", original_name="f.pdf")
        att.bill_attachments.exists.return_value = not orphan
        att.payment_attachments.exists.return_value = not orphan
        att.delete.side_effect=lambda: order.append("attachment.delete")
        mapping=MagicMock(attachment=att, xero_attachment_id=xero_id)
        mapping.delete.side_effect=lambda: order.append("mapping.delete")
        s3=MagicMock()
        if s3_fails:
            s3.delete_object.side_effect=ClientError({"Error":{"Code":"500","Message":"boom"}},"DeleteObject")
        else:
            s3.delete_object.side_effect=lambda **kw: order.append(f"s3.delete:{kw.get('Key')}")
        bill=MagicMock(id="B1"); pay=MagicMock(id="P1")
        rel = bill.bill_attachments if kind=="bill" else pay.payment_attachments
        if missing:
            from bills.models import BillAttachment, PaymentAttachment
            rel.select_related.return_value.get.side_effect = (
                BillAttachment.DoesNotExist if kind=="bill" else PaymentAttachment.DoesNotExist)
        else:
            rel.select_related.return_value.get.return_value = mapping
        with patch.object(A,"_get_s3_client",return_value=s3), \
             patch.object(A,"log_audit") as la, \
             patch.object(A,"transaction") as tx:
            tx.atomic.return_value.__enter__=lambda s:None
            tx.atomic.return_value.__exit__=lambda s,*a:None
            if kind=="bill": res["return"]=repr(A.delete_attachment(bill,"M1","U1"))
            else:            res["return"]=repr(A.delete_payment_attachment(pay,"M1","U1"))
            res["audit"]=[str(c) for c in la.call_args_list]
    except BillValidationError as e: res["error"]=str(e)
    except Exception as e: res["exc"]=f"{type(e).__name__}: {e}"
    finally: lg.removeHandler(cap)
    res["order"]=order; res["logs"]=cap.recs
    return res

out={}
for kind in ("bill","payment"):
    for missing in (False,True):
        for s3f in (False,True):
            for orphan in (True,False):
                out[f"{kind}|missing={missing}|s3fail={s3f}|orphan={orphan}"]=run(kind,missing,s3f,orphan)
    out[f"{kind}|no_xero_id"]=run(kind,xero_id="")
json.dump(out,open(sys.argv[1],"w"),indent=1,sort_keys=True,default=str)
print(f"{len(out)} cases -> {sys.argv[1]}")

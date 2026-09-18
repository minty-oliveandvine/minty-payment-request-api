# Archive — the pettycashv2 billing design (superseded 2026-09-17)

The billing database design as it stood on the `pettycashv2` schema, committed
2026-06-22 ("start of prestaging"):

| File | What it was |
|---|---|
| `db_design.md` | The design doc for the bills tables, shared with the Flask app on `pettycashv2`. |
| `db_design_ko.pdf` | Korean rendering of the above. |
| `Minty_Billing_DB_Design_EN_v3.md` | English version (converted from the Word original on 2026-09-18; the `.docx` is gone). |
| `generate_db_design_pdf.py` | The one-off `fpdf` script that rendered the PDF. `fpdf` is not in `requirements.txt` and the script hard-codes `C:/Windows/Fonts/malgun.ttf`; it only ever ran on one machine. |

Superseded when the repo moved to `pettycashv3` (`525262a` "Switch schema references
to pettycashv3", `2489cb5` "Rebase bills app to C8 schema"). The live schema is
`Minty/docs/schema/01_schema_rebased.sql`; this app's tables are section C8 of it.
Nothing here is referenced from code.

"""Generate db_design_ko.pdf — Korean translation of the billing DB design."""

from fpdf import FPDF

FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
FONT_PATH_BOLD = "C:/Windows/Fonts/malgunbd.ttf"


class PDF(FPDF):
    def header(self):
        self.set_font("malgun", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "Minty Billing — 데이터베이스 설계서", align="R")
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("malgun", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"페이지 {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title):
        self.set_font("malgun", "B", 14)
        self.set_text_color(30, 30, 30)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(84, 211, 218)
        self.set_line_width(0.8)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(6)

    def sub_title(self, title):
        self.set_font("malgun", "B", 11)
        self.set_text_color(50, 50, 50)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text):
        self.set_font("malgun", "", 9)
        self.set_text_color(60, 60, 60)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def add_table(self, headers, rows, col_widths=None):
        if col_widths is None:
            col_widths = [self.epw / len(headers)] * len(headers)

        self.set_font("malgun", "B", 8)
        self.set_fill_color(84, 211, 218)
        self.set_text_color(255, 255, 255)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, h, border=1, fill=True, align="C")
        self.ln()

        self.set_font("malgun", "", 8)
        self.set_text_color(60, 60, 60)
        fill = False
        for row in rows:
            if self.get_y() > 260:
                self.add_page()
            if fill:
                self.set_fill_color(245, 245, 245)
            else:
                self.set_fill_color(255, 255, 255)
            max_h = 6
            for i, cell_text in enumerate(row):
                self.cell(col_widths[i], max_h, str(cell_text), border=1, fill=True)
            self.ln()
            fill = not fill

    def code_block(self, text):
        self.set_font("malgun", "", 7.5)
        self.set_text_color(40, 40, 40)
        self.set_fill_color(245, 245, 250)
        for line in text.strip().split("\n"):
            if self.get_y() > 270:
                self.add_page()
            self.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(3)


def build_pdf():
    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("malgun", "", FONT_PATH)
    pdf.add_font("malgun", "B", FONT_PATH_BOLD)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Title ──
    pdf.set_font("malgun", "B", 20)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 15, "Minty Billing", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("malgun", "", 13)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "데이터베이스 설계서 (Database Design)", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("malgun", "", 9)
    pdf.cell(0, 6, "스키마: pettycashv2  |  엔진: PostgreSQL 15  |  PK: UUID v4 (varchar 36)", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # ── 1. Table Overview ──
    pdf.section_title("1. 테이블 개요")
    pdf.body_text("Module 1(Flask)에서 관리하는 기존 테이블과 Module 2(Django)에서 관리하는 신규 테이블로 구성됩니다.")

    pdf.add_table(
        ["테이블명", "관리 주체", "설명"],
        [
            ["user", "Flask (모듈 1)", "사용자 계정, 인증, Xero 토큰"],
            ["entities", "Flask (모듈 1)", "사업체 / 조직"],
            ["user_entity", "Flask (모듈 1)", "사용자 ↔ 엔티티 접근 (M2M)"],
            ["account_info", "Flask (모듈 1)", "엔티티별 Xero 계정과목"],
            ["xero_contact_sync", "Flask (모듈 1)", "엔티티별 Xero 연락처"],
            ["bill", "Django (모듈 2)", "청구서 레코드"],
            ["bill_line_item", "Django (모듈 2)", "청구서별 항목"],
            ["attachment", "Django (모듈 2)", "업로드된 인보이스 파일"],
            ["audit", "Django (모듈 2)", "감사 추적 기록"],
        ],
        col_widths=[40, 40, 110],
    )
    pdf.ln(6)

    # ── 2. bill table ──
    pdf.section_title("2. bill 테이블 (청구서)")
    pdf.body_text("각 청구서의 핵심 정보를 저장합니다. entity_id로 사업체를 구분하고, status로 생애주기를 관리합니다.")

    pdf.add_table(
        ["컬럼명", "타입", "제약조건", "설명"],
        [
            ["id", "varchar(36)", "PK, uuid4", "고유 식별자"],
            ["entity_id", "varchar(36)", "NOT NULL, INDEX", "사업체 ID (→ entities)"],
            ["contact", "varchar(50)", "기본값 ''", "거래처 / 공급업체명"],
            ["xero_contact_id", "varchar(36)", "기본값 ''", "Xero 연락처 ID"],
            ["status", "varchar(50)", "기본값 'draft'", "draft / submitted / paid"],
            ["amount", "decimal(10,2)", "기본값 0", "총 청구 금액"],
            ["description", "text", "기본값 ''", "청구서 설명"],
            ["due_date", "datetime", "NULL 허용", "결제 기한"],
            ["invoice_date", "datetime", "NULL 허용", "인보이스 날짜"],
            ["paid_date", "datetime", "NULL 허용", "결제 완료 일시"],
            ["uploaded_by", "varchar(50)", "NOT NULL, INDEX", "등록자 (→ user.id)"],
            ["attachment_id", "varchar(36)", "FK, NULL 허용", "첨부파일 (→ attachment)"],
            ["published", "varchar(50)", "기본값 'not_published'", "Xero 게시 상태"],
            ["xero_invoice_id", "varchar(36)", "기본값 ''", "Xero 인보이스 ID"],
            ["created_at", "datetime", "자동, NOT NULL", "생성 일시"],
            ["updated_at", "datetime", "자동, NOT NULL", "수정 일시"],
        ],
        col_widths=[32, 28, 38, 92],
    )
    pdf.ln(6)

    # ── 3. bill_line_item table ──
    pdf.section_title("3. bill_line_item 테이블 (청구 항목)")
    pdf.body_text("각 청구서에 속한 개별 항목입니다. 청구서 삭제 시 함께 삭제됩니다 (CASCADE).")

    pdf.add_table(
        ["컬럼명", "타입", "제약조건", "설명"],
        [
            ["id", "varchar(36)", "PK, uuid4", "고유 식별자"],
            ["bill_id", "varchar(36)", "FK, CASCADE", "상위 청구서 (→ bill)"],
            ["description", "varchar(255)", "NOT NULL", "항목 설명"],
            ["amount", "decimal(10,2)", "NOT NULL", "항목 금액"],
            ["xero_account_id", "varchar(100)", "기본값 ''", "Xero 계정 코드"],
            ["xero_account_name", "varchar(150)", "기본값 ''", "Xero 계정명"],
        ],
        col_widths=[35, 28, 35, 92],
    )
    pdf.ln(6)

    # ── 4. attachment table ──
    pdf.section_title("4. attachment 테이블 (첨부파일)")
    pdf.body_text("업로드된 인보이스 파일 (이미지 또는 PDF)의 메타데이터를 저장합니다.")

    pdf.add_table(
        ["컬럼명", "타입", "제약조건", "설명"],
        [
            ["id", "varchar(36)", "PK, uuid4", "고유 식별자"],
            ["original_name", "varchar(200)", "NOT NULL", "원본 파일명"],
            ["stored_name", "varchar(150)", "NOT NULL", "저장된 파일명 (UUID)"],
            ["path", "text", "NOT NULL", "저장 경로 (상대)"],
            ["type", "varchar(50)", "NOT NULL", "MIME 타입"],
            ["size", "integer", "NOT NULL", "파일 크기 (바이트)"],
            ["uploaded_by", "varchar(150)", "NOT NULL", "등록자 (→ user.id)"],
            ["created_at", "datetime", "자동, NOT NULL", "업로드 일시"],
        ],
        col_widths=[30, 28, 35, 97],
    )
    pdf.ln(6)

    # ── 5. audit table ──
    pdf.section_title("5. audit 테이블 (감사 추적)")
    pdf.body_text("청구서에 대한 모든 작업 이력을 시간순으로 기록합니다. 삭제 또는 수정할 수 없습니다.")

    pdf.add_table(
        ["컬럼명", "타입", "제약조건", "설명"],
        [
            ["id", "varchar(36)", "PK, uuid4", "고유 식별자"],
            ["bill_id", "varchar(36)", "FK, CASCADE", "대상 청구서 (→ bill)"],
            ["action", "varchar(100)", "NOT NULL", "작업 유형 (아래 참조)"],
            ["detail", "text", "기본값 ''", "변경 내용 (JSON)"],
            ["date", "datetime", "자동, NOT NULL", "작업 시각"],
            ["user_id", "varchar(36)", "NOT NULL", "수행자 (→ user.id)"],
        ],
        col_widths=[30, 28, 35, 97],
    )
    pdf.ln(4)

    pdf.sub_title("감사 액션 유형")
    pdf.add_table(
        ["액션", "발생 시점"],
        [
            ["created", "청구서 최초 저장"],
            ["edited", "필드 수정 (detail = JSON diff)"],
            ["submitted", "초안 → 제출됨"],
            ["marked_paid", "제출됨 → 결제 완료"],
            ["published_to_xero", "Xero API에 게시"],
            ["attachment_uploaded", "인보이스 파일 첨부"],
            ["attachment_deleted", "첨부파일 삭제"],
        ],
        col_widths=[50, 140],
    )
    pdf.ln(6)

    # ── 6. Status Lifecycle ──
    pdf.section_title("6. 상태 생애주기 (Status Lifecycle)")

    pdf.body_text(
        "청구서는 아래 3단계 상태를 순차적으로 거칩니다.\n"
        "결제 완료(paid) 후에는 어떤 역할도 수정할 수 없습니다 (절대적 불변성)."
    )

    pdf.code_block(
        "  DRAFT (초안)                       editable (수정 가능)\n"
        "    │\n"
        "    │  POST /bills/{id}/submit\n"
        "    │  필수: 첨부파일 + 완전한 항목\n"
        "    ▼\n"
        "  SUBMITTED (제출됨)                  editable (수정 가능)\n"
        "    │\n"
        "    │  POST /bills/{id}/mark-paid\n"
        "    │  paid_date 설정, 레코드 잠금\n"
        "    ▼\n"
        "  PAID (결제 완료)                    IMMUTABLE (불변)\n"
        "\n"
        "  게시(Published) 플래그는 독립적:\n"
        "  not_published ──POST /xero/publish/{id}──► published\n"
        "  (완전한 항목 + 첨부파일이 있는 모든 상태에서 가능)"
    )
    pdf.ln(4)

    # ── 7. Cross-module FKs ──
    pdf.section_title("7. 모듈 간 논리적 외래키")
    pdf.body_text(
        "아래 관계는 데이터베이스 수준에서 강제되지 않습니다. "
        "Module 1과 Module 2의 결합도를 낮추기 위해 애플리케이션 코드에서 검증합니다."
    )

    pdf.add_table(
        ["모듈 2 컬럼", "참조 방향", "모듈 1 테이블"],
        [
            ["bill.entity_id", "→", "entities.id"],
            ["bill.uploaded_by", "→", "user.id"],
            ["bill.xero_contact_id", "→", "xero_contact_sync.xero_contact_id"],
            ["audit.user_id", "→", "user.id"],
            ["attachment.uploaded_by", "→", "user.id"],
            ["bill_line_item.xero_account_id", "→", "account_info.xero_account_id"],
        ],
        col_widths=[55, 20, 115],
    )
    pdf.ln(6)

    # ── 8. SQL DDL ──
    pdf.section_title("8. SQL DDL (PostgreSQL)")
    pdf.body_text("Django 마이그레이션이 자동 생성하지만, 수동 생성 시 아래 SQL을 사용할 수 있습니다.")

    pdf.code_block(
        "CREATE TABLE pettycashv2.attachment (\n"
        "    id              VARCHAR(36)  PRIMARY KEY,\n"
        "    original_name   VARCHAR(200) NOT NULL,\n"
        "    stored_name     VARCHAR(150) NOT NULL,\n"
        "    path            TEXT         NOT NULL,\n"
        "    type            VARCHAR(50)  NOT NULL,\n"
        "    size            INTEGER      NOT NULL,\n"
        "    uploaded_by     VARCHAR(150) NOT NULL,\n"
        "    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()\n"
        ");"
    )

    pdf.code_block(
        "CREATE TABLE pettycashv2.bill (\n"
        "    id              VARCHAR(36)    PRIMARY KEY,\n"
        "    entity_id       VARCHAR(36)    NOT NULL,\n"
        "    contact         VARCHAR(50)    NOT NULL DEFAULT '',\n"
        "    xero_contact_id VARCHAR(36)    NOT NULL DEFAULT '',\n"
        "    status          VARCHAR(50)    NOT NULL DEFAULT 'draft',\n"
        "    amount          NUMERIC(10,2)  NOT NULL DEFAULT 0,\n"
        "    description     TEXT           NOT NULL DEFAULT '',\n"
        "    due_date        TIMESTAMPTZ,\n"
        "    invoice_date    TIMESTAMPTZ,\n"
        "    paid_date       TIMESTAMPTZ,\n"
        "    uploaded_by     VARCHAR(50)    NOT NULL,\n"
        "    attachment_id   VARCHAR(36)    REFERENCES pettycashv2.attachment(id)\n"
        "                                  ON DELETE SET NULL,\n"
        "    published       VARCHAR(50)    NOT NULL DEFAULT 'not_published',\n"
        "    xero_invoice_id VARCHAR(36)    NOT NULL DEFAULT '',\n"
        "    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),\n"
        "    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()\n"
        ");\n"
        "\n"
        "CREATE INDEX idx_bill_entity_id   ON pettycashv2.bill (entity_id);\n"
        "CREATE INDEX idx_bill_uploaded_by ON pettycashv2.bill (uploaded_by);\n"
        "CREATE INDEX idx_bill_status      ON pettycashv2.bill (status);\n"
        "CREATE INDEX idx_bill_created_at  ON pettycashv2.bill (created_at DESC);"
    )

    pdf.code_block(
        "CREATE TABLE pettycashv2.bill_line_item (\n"
        "    id                VARCHAR(36)   PRIMARY KEY,\n"
        "    bill_id           VARCHAR(36)   NOT NULL REFERENCES pettycashv2.bill(id)\n"
        "                                   ON DELETE CASCADE,\n"
        "    description       VARCHAR(255)  NOT NULL,\n"
        "    amount            NUMERIC(10,2) NOT NULL,\n"
        "    xero_account_id   VARCHAR(100)  NOT NULL DEFAULT '',\n"
        "    xero_account_name VARCHAR(150)  NOT NULL DEFAULT ''\n"
        ");\n"
        "\n"
        "CREATE INDEX idx_line_item_bill_id ON pettycashv2.bill_line_item (bill_id);"
    )

    pdf.code_block(
        "CREATE TABLE pettycashv2.audit (\n"
        "    id       VARCHAR(36)  PRIMARY KEY,\n"
        "    bill_id  VARCHAR(36)  NOT NULL REFERENCES pettycashv2.bill(id)\n"
        "                         ON DELETE CASCADE,\n"
        "    action   VARCHAR(100) NOT NULL,\n"
        "    detail   TEXT         NOT NULL DEFAULT '',\n"
        "    date     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),\n"
        "    user_id  VARCHAR(36)  NOT NULL\n"
        ");\n"
        "\n"
        "CREATE INDEX idx_audit_bill_id ON pettycashv2.audit (bill_id);\n"
        "CREATE INDEX idx_audit_date    ON pettycashv2.audit (date);"
    )

    output_path = "docs/db_design_ko.pdf"
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    build_pdf()

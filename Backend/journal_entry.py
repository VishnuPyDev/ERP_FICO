"""
STACKLY OneERP - FICO Journal Entry API
File: journal_entry.py

Uses the existing SQLAlchemy database.py:
    from database import get_db, engine

Existing tables expected:
    company(id, company_code)
    chart_of_accounts(id, company_code, account_code, account_name,
                      account_type, normal_balance, is_active)
    fico_fiscal_years(id, company_id, start_date, end_date, status)
    fico_fiscal_periods(id, fiscal_year_id, company_id, start_date,
                        end_date, status)

New tables created automatically:
    fico_journal_entries
    fico_journal_entry_lines

Register in main.py:
    from journal_entry import router as journal_entry_router
    app.include_router(journal_entry_router)

This module stores posted journal lines as the accounting source of truth.
It does not write to an unknown/legacy General Ledger table. The Trial Balance
endpoint is calculated from posted journal lines and can later be used by GL
and Financial Reports.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

try:
    from database import get_db, engine
except ImportError:
    from .database import get_db, engine


router = APIRouter(prefix="/api/journal-entries", tags=["Journal Entries"])

MONEY = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999999.99")
ALLOWED_CURRENCIES = {"INR", "USD", "EUR", "GBP", "AED", "SGD"}
ENTRY_STATUSES = {"DRAFT", "POSTED", "REVERSED"}


def money(value) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422, "Invalid monetary amount")
    if not result.is_finite() or result < 0 or result > MAX_AMOUNT:
        raise HTTPException(422, "Amount must be between 0 and 9999999999999.99")
    return result


def ensure_tables():
    """Create only the two journal tables; do not alter existing ERP tables."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fico_journal_entries (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                entry_number VARCHAR(40) NULL,
                company_id INT NOT NULL,
                company_code VARCHAR(30) NOT NULL,
                fiscal_year_id INT NOT NULL,
                fiscal_period_id INT NOT NULL,
                entry_date DATE NOT NULL,
                posting_date DATE NOT NULL,
                currency CHAR(3) NOT NULL DEFAULT 'INR',
                reference_number VARCHAR(60) NULL,
                description VARCHAR(255) NOT NULL,
                total_debit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                total_credit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
                entry_type VARCHAR(20) NOT NULL DEFAULT 'STANDARD',
                reversal_of_id BIGINT NULL,
                created_by VARCHAR(100) NULL,
                posted_by VARCHAR(100) NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP,
                posted_at DATETIME NULL,
                UNIQUE KEY uq_fico_journal_entry_number (entry_number),
                UNIQUE KEY uq_fico_journal_reversal (reversal_of_id),
                INDEX ix_fico_journal_company_date (company_id, entry_date),
                INDEX ix_fico_journal_status (company_id, status),
                INDEX ix_fico_journal_period (fiscal_period_id)
            ) ENGINE=InnoDB
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS fico_journal_entry_lines (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                journal_entry_id BIGINT NOT NULL,
                line_number INT NOT NULL,
                account_id INT NOT NULL,
                account_code VARCHAR(30) NOT NULL,
                account_name VARCHAR(150) NOT NULL,
                line_description VARCHAR(255) NULL,
                debit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                credit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                cost_center VARCHAR(50) NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_fico_journal_line (journal_entry_id, line_number),
                INDEX ix_fico_journal_line_account (account_id),
                CONSTRAINT fk_fico_journal_line_header
                    FOREIGN KEY (journal_entry_id)
                    REFERENCES fico_journal_entries(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
        """))


@router.on_event("startup")
def startup():
    ensure_tables()


class JournalLineIn(BaseModel):
    account_id: int = Field(gt=0)
    line_description: Optional[str] = Field(None, max_length=255)
    debit: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=15, decimal_places=2)
    credit: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=15, decimal_places=2)
    cost_center: Optional[str] = Field(None, max_length=50)

    @field_validator("line_description", "cost_center")
    @classmethod
    def strip_optional(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def exactly_one_side(self):
        d = money(self.debit)
        c = money(self.credit)
        if (d > 0 and c > 0) or (d == 0 and c == 0):
            raise ValueError("Each line must contain either a debit or a credit amount, not both")
        return self


class JournalCreate(BaseModel):
    company_id: int = Field(gt=0)
    fiscal_year_id: int = Field(gt=0)
    fiscal_period_id: int = Field(gt=0)
    entry_date: date
    posting_date: date
    currency: str = Field(default="INR", min_length=3, max_length=3)
    reference_number: Optional[str] = Field(None, max_length=60)
    description: str = Field(min_length=3, max_length=255)
    created_by: Optional[str] = Field(None, max_length=100)
    lines: List[JournalLineIn] = Field(min_length=2, max_length=200)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value):
        value = value.strip().upper()
        if value not in ALLOWED_CURRENCIES:
            raise ValueError(f"Currency must be one of: {', '.join(sorted(ALLOWED_CURRENCIES))}")
        return value

    @field_validator("description", "reference_number", "created_by")
    @classmethod
    def clean_text(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_totals_and_dates(self):
        if self.posting_date < self.entry_date:
            raise ValueError("Posting date cannot be earlier than entry date")
        debit_total = sum((money(line.debit) for line in self.lines), Decimal("0.00"))
        credit_total = sum((money(line.credit) for line in self.lines), Decimal("0.00"))
        if debit_total <= 0 or debit_total != credit_total:
            raise ValueError("Journal entry must have equal debit and credit totals greater than zero")
        return self


class JournalUpdate(BaseModel):
    fiscal_year_id: Optional[int] = Field(None, gt=0)
    fiscal_period_id: Optional[int] = Field(None, gt=0)
    entry_date: Optional[date] = None
    posting_date: Optional[date] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    reference_number: Optional[str] = Field(None, max_length=60)
    description: Optional[str] = Field(None, min_length=3, max_length=255)
    created_by: Optional[str] = Field(None, max_length=100)
    lines: Optional[List[JournalLineIn]] = Field(None, min_length=2, max_length=200)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value):
        if value is None:
            return value
        value = value.strip().upper()
        if value not in ALLOWED_CURRENCIES:
            raise ValueError("Unsupported currency")
        return value

    @model_validator(mode="after")
    def validate_if_lines_supplied(self):
        if self.lines is not None:
            debit_total = sum((money(line.debit) for line in self.lines), Decimal("0.00"))
            credit_total = sum((money(line.credit) for line in self.lines), Decimal("0.00"))
            if debit_total <= 0 or debit_total != credit_total:
                raise ValueError("Journal entry must have equal debit and credit totals greater than zero")
        if self.entry_date and self.posting_date and self.posting_date < self.entry_date:
            raise ValueError("Posting date cannot be earlier than entry date")
        return self


def company_record(db: Session, company_id: int):
    row = db.execute(text("""
        SELECT id, company_code
        FROM company
        WHERE id = :company_id
        LIMIT 1
    """), {"company_id": company_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Company not found. Check the company table and Company ID.")
    return dict(row)


def get_fiscal_context(db: Session, company_id: int, fiscal_year_id: int,
                       fiscal_period_id: int, entry_date: date, posting_date: date):
    year = db.execute(text("""
        SELECT id, company_id, start_date, end_date, status
        FROM fico_fiscal_years
        WHERE id = :year_id AND company_id = :company_id
    """), {"year_id": fiscal_year_id, "company_id": company_id}).mappings().first()
    if not year:
        raise HTTPException(404, "Fiscal year not found for this company")

    period = db.execute(text("""
        SELECT id, fiscal_year_id, company_id, start_date, end_date, status
        FROM fico_fiscal_periods
        WHERE id = :period_id AND fiscal_year_id = :year_id
          AND company_id = :company_id
    """), {
        "period_id": fiscal_period_id,
        "year_id": fiscal_year_id,
        "company_id": company_id
    }).mappings().first()
    if not period:
        raise HTTPException(404, "Fiscal period not found for the selected company and fiscal year")

    if entry_date < year["start_date"] or entry_date > year["end_date"]:
        raise HTTPException(422, "Entry date must be within the selected fiscal year")
    if posting_date < year["start_date"] or posting_date > year["end_date"]:
        raise HTTPException(422, "Posting date must be within the selected fiscal year")
    if entry_date < period["start_date"] or entry_date > period["end_date"]:
        raise HTTPException(422, "Entry date must be within the selected fiscal period")
    if posting_date < period["start_date"] or posting_date > period["end_date"]:
        raise HTTPException(422, "Posting date must be within the selected fiscal period")
    return dict(year), dict(period)


def load_account_lines(db: Session, company_code: str, lines: List[JournalLineIn]):
    account_ids = [line.account_id for line in lines]
    if len(account_ids) != len(set(account_ids)):
        # Repeating an account is sometimes valid in accounting, so do not reject it.
        pass

    loaded = {}
    for line in lines:
        if line.account_id not in loaded:
            row = db.execute(text("""
                SELECT id, company_code, account_code, account_name,
                       account_type, normal_balance, is_active
                FROM chart_of_accounts
                WHERE id = :account_id AND company_code = :company_code
                LIMIT 1
            """), {
                "account_id": line.account_id,
                "company_code": company_code
            }).mappings().first()
            if not row:
                raise HTTPException(422, f"Account ID {line.account_id} does not belong to company {company_code}")
            if not bool(row["is_active"]):
                raise HTTPException(422, f"Account {row['account_code']} is inactive")
            loaded[line.account_id] = dict(row)
    return loaded


def totals(lines):
    debit = sum((money(x.debit) for x in lines), Decimal("0.00"))
    credit = sum((money(x.credit) for x in lines), Decimal("0.00"))
    if debit <= 0 or debit != credit:
        raise HTTPException(422, "Total debit and total credit must be equal and greater than zero")
    return debit, credit


def get_entry(db: Session, entry_id: int, include_lines=True):
    header = db.execute(text("""
        SELECT * FROM fico_journal_entries WHERE id = :entry_id
    """), {"entry_id": entry_id}).mappings().first()
    if not header:
        raise HTTPException(404, "Journal entry not found")
    result = dict(header)
    if include_lines:
        rows = db.execute(text("""
            SELECT id, journal_entry_id, line_number, account_id,
                   account_code, account_name, line_description,
                   debit, credit, cost_center
            FROM fico_journal_entry_lines
            WHERE journal_entry_id = :entry_id
            ORDER BY line_number
        """), {"entry_id": entry_id}).mappings().all()
        result["lines"] = [dict(row) for row in rows]
    return result


def save_lines(db: Session, entry_id: int, lines: List[JournalLineIn], accounts):
    db.execute(text("""
        DELETE FROM fico_journal_entry_lines
        WHERE journal_entry_id = :entry_id
    """), {"entry_id": entry_id})

    for index, line in enumerate(lines, start=1):
        account = accounts[line.account_id]
        db.execute(text("""
            INSERT INTO fico_journal_entry_lines
                (journal_entry_id, line_number, account_id, account_code,
                 account_name, line_description, debit, credit, cost_center)
            VALUES
                (:entry_id, :line_number, :account_id, :account_code,
                 :account_name, :line_description, :debit, :credit, :cost_center)
        """), {
            "entry_id": entry_id,
            "line_number": index,
            "account_id": line.account_id,
            "account_code": account["account_code"],
            "account_name": account["account_name"],
            "line_description": line.line_description,
            "debit": money(line.debit),
            "credit": money(line.credit),
            "cost_center": line.cost_center
        })


@router.get("/health")
def journal_health():
    return {"module": "Journal Entry", "status": "ready"}


@router.get("/accounts")
def journal_accounts(company_id: int = Query(gt=0), search: Optional[str] = None,
                     db: Session = Depends(get_db)):
    company = company_record(db, company_id)
    sql = """
        SELECT id, company_code, account_code, account_name,
               account_type, normal_balance, is_active
        FROM chart_of_accounts
        WHERE company_code = :company_code AND is_active = TRUE
    """
    params = {"company_code": company["company_code"]}
    if search and search.strip():
        sql += " AND (account_code LIKE :term OR account_name LIKE :term)"
        params["term"] = f"%{search.strip()}%"
    sql += " ORDER BY account_code"
    return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


@router.get("/summary")
def journal_summary(company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    company_record(db, company_id)
    row = db.execute(text("""
        SELECT
            COUNT(*) AS total_entries,
            SUM(status = 'DRAFT') AS draft_entries,
            SUM(status = 'POSTED') AS posted_entries,
            SUM(status = 'REVERSED') AS reversed_entries,
            COALESCE(SUM(CASE WHEN status = 'POSTED' THEN total_debit ELSE 0 END), 0) AS posted_debit,
            COALESCE(SUM(CASE WHEN status = 'POSTED' THEN total_credit ELSE 0 END), 0) AS posted_credit
        FROM fico_journal_entries
        WHERE company_id = :company_id
    """), {"company_id": company_id}).mappings().one()
    return {k: (float(v) if isinstance(v, Decimal) else int(v or 0))
            for k, v in dict(row).items()}


@router.get("/")
def list_journal_entries(
    company_id: int = Query(gt=0),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    company_record(db, company_id)
    sql = "SELECT * FROM fico_journal_entries WHERE company_id = :company_id"
    params = {"company_id": company_id, "limit": limit, "offset": offset}
    if status_filter:
        normalized = status_filter.strip().upper()
        if normalized not in ENTRY_STATUSES:
            raise HTTPException(422, "Status must be DRAFT, POSTED, or REVERSED")
        sql += " AND status = :status"
        params["status"] = normalized
    if search and search.strip():
        sql += " AND (entry_number LIKE :term OR description LIKE :term OR reference_number LIKE :term)"
        params["term"] = f"%{search.strip()}%"
    if date_from:
        sql += " AND entry_date >= :date_from"
        params["date_from"] = date_from
    if date_to:
        sql += " AND entry_date <= :date_to"
        params["date_to"] = date_to
    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "date_to must be on or after date_from")
    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


@router.get("/reports/trial-balance")
def trial_balance(company_id: int = Query(gt=0),
                  fiscal_year_id: Optional[int] = Query(None, gt=0),
                  db: Session = Depends(get_db)):
    company = company_record(db, company_id)
    sql = """
        SELECT l.account_id, l.account_code, l.account_name,
               SUM(l.debit) AS total_debit,
               SUM(l.credit) AS total_credit
        FROM fico_journal_entry_lines l
        JOIN fico_journal_entries h ON h.id = l.journal_entry_id
        WHERE h.company_id = :company_id
          AND h.status IN ('POSTED', 'REVERSED')
    """
    params = {"company_id": company_id}
    if fiscal_year_id:
        sql += " AND h.fiscal_year_id = :fiscal_year_id"
        params["fiscal_year_id"] = fiscal_year_id
    sql += """
        GROUP BY l.account_id, l.account_code, l.account_name
        ORDER BY l.account_code
    """
    rows = db.execute(text(sql), params).mappings().all()
    result = []
    for row in rows:
        debit = money(row["total_debit"] or 0)
        credit = money(row["total_credit"] or 0)
        net = debit - credit
        result.append({
            "account_id": row["account_id"],
            "account_code": row["account_code"],
            "account_name": row["account_name"],
            "total_debit": float(debit),
            "total_credit": float(credit),
            "net_debit": float(net if net > 0 else 0),
            "net_credit": float(-net if net < 0 else 0)
        })
    return {"company_code": company["company_code"], "rows": result,
            "total_debit": round(sum(x["total_debit"] for x in result), 2),
            "total_credit": round(sum(x["total_credit"] for x in result), 2)}


@router.get("/{entry_id}")
def read_journal_entry(entry_id: int, db: Session = Depends(get_db)):
    return get_entry(db, entry_id)


@router.post("/", status_code=201)
def create_journal_entry(payload: JournalCreate, db: Session = Depends(get_db)):
    company = company_record(db, payload.company_id)
    get_fiscal_context(
        db, payload.company_id, payload.fiscal_year_id,
        payload.fiscal_period_id, payload.entry_date, payload.posting_date
    )
    debit_total, credit_total = totals(payload.lines)
    accounts = load_account_lines(db, company["company_code"], payload.lines)

    try:
        result = db.execute(text("""
            INSERT INTO fico_journal_entries
                (company_id, company_code, fiscal_year_id, fiscal_period_id,
                 entry_date, posting_date, currency, reference_number,
                 description, total_debit, total_credit, status, created_by)
            VALUES
                (:company_id, :company_code, :fiscal_year_id, :fiscal_period_id,
                 :entry_date, :posting_date, :currency, :reference_number,
                 :description, :total_debit, :total_credit, 'DRAFT', :created_by)
        """), {
            "company_id": payload.company_id,
            "company_code": company["company_code"],
            "fiscal_year_id": payload.fiscal_year_id,
            "fiscal_period_id": payload.fiscal_period_id,
            "entry_date": payload.entry_date,
            "posting_date": payload.posting_date,
            "currency": payload.currency,
            "reference_number": payload.reference_number,
            "description": payload.description,
            "total_debit": debit_total,
            "total_credit": credit_total,
            "created_by": payload.created_by
        })
        entry_id = result.lastrowid
        entry_number = f"JE-{company['company_code']}-{entry_id:08d}"
        db.execute(text("""
            UPDATE fico_journal_entries
            SET entry_number = :entry_number
            WHERE id = :entry_id
        """), {"entry_number": entry_number, "entry_id": entry_id})
        save_lines(db, entry_id, payload.lines, accounts)
        db.commit()
        return get_entry(db, entry_id)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Journal entry conflicts with an existing database record") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not save journal entry. Check journal table structure.") from exc


@router.put("/{entry_id}")
def update_journal_entry(entry_id: int, payload: JournalUpdate,
                         db: Session = Depends(get_db)):
    old = get_entry(db, entry_id)
    if old["status"] != "DRAFT":
        raise HTTPException(409, "Only Draft journal entries can be edited")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return old

    company_id = old["company_id"]
    fy_id = changes.get("fiscal_year_id", old["fiscal_year_id"])
    period_id = changes.get("fiscal_period_id", old["fiscal_period_id"])
    entry_date = changes.get("entry_date", old["entry_date"])
    posting_date = changes.get("posting_date", old["posting_date"])
    get_fiscal_context(db, company_id, fy_id, period_id, entry_date, posting_date)

    current_lines = payload.lines if payload.lines is not None else [
        JournalLineIn(
            account_id=line["account_id"],
            line_description=line["line_description"],
            debit=line["debit"],
            credit=line["credit"],
            cost_center=line["cost_center"]
        ) for line in old["lines"]
    ]
    debit_total, credit_total = totals(current_lines)
    company = company_record(db, company_id)
    accounts = load_account_lines(db, company["company_code"], current_lines)

    header_fields = {
        "fiscal_year_id": fy_id,
        "fiscal_period_id": period_id,
        "entry_date": entry_date,
        "posting_date": posting_date,
        "currency": changes.get("currency", old["currency"]),
        "reference_number": changes.get("reference_number", old["reference_number"]),
        "description": changes.get("description", old["description"]),
        "created_by": changes.get("created_by", old["created_by"]),
        "total_debit": debit_total,
        "total_credit": credit_total,
        "entry_id": entry_id
    }
    try:
        db.execute(text("""
            UPDATE fico_journal_entries
            SET fiscal_year_id=:fiscal_year_id,
                fiscal_period_id=:fiscal_period_id,
                entry_date=:entry_date, posting_date=:posting_date,
                currency=:currency, reference_number=:reference_number,
                description=:description, created_by=:created_by,
                total_debit=:total_debit, total_credit=:total_credit
            WHERE id=:entry_id AND status='DRAFT'
        """), header_fields)
        save_lines(db, entry_id, current_lines, accounts)
        db.commit()
        return get_entry(db, entry_id)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not update draft journal entry") from exc


@router.delete("/{entry_id}")
def delete_draft_entry(entry_id: int, db: Session = Depends(get_db)):
    old = get_entry(db, entry_id, include_lines=False)
    if old["status"] != "DRAFT":
        raise HTTPException(409, "Only Draft journal entries can be deleted")
    try:
        db.execute(text("DELETE FROM fico_journal_entries WHERE id=:id AND status='DRAFT'"),
                   {"id": entry_id})
        db.commit()
        return {"message": "Draft journal entry deleted", "entry_id": entry_id}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not delete draft journal entry") from exc


@router.post("/{entry_id}/post")
def post_journal_entry(entry_id: int, posted_by: Optional[str] = Query(None, max_length=100),
                       db: Session = Depends(get_db)):
    entry = get_entry(db, entry_id)
    if entry["status"] != "DRAFT":
        raise HTTPException(409, "Only Draft journal entries can be posted")

    # Re-check period/year immediately before posting.
    year, period = get_fiscal_context(
        db, entry["company_id"], entry["fiscal_year_id"],
        entry["fiscal_period_id"], entry["entry_date"], entry["posting_date"]
    )
    if str(year["status"]).lower() != "open":
        raise HTTPException(409, "Fiscal year must be Open before posting")
    if str(period["status"]).lower() != "open":
        raise HTTPException(409, "Fiscal period must be Open before posting")

    debit_total, credit_total = totals([
        JournalLineIn(
            account_id=line["account_id"],
            debit=line["debit"],
            credit=line["credit"],
            line_description=line["line_description"],
            cost_center=line["cost_center"]
        ) for line in entry["lines"]
    ])
    if debit_total != money(entry["total_debit"]) or credit_total != money(entry["total_credit"]):
        raise HTTPException(409, "Journal totals do not match the saved journal lines")

    try:
        result = db.execute(text("""
            UPDATE fico_journal_entries
            SET status='POSTED', posted_by=:posted_by, posted_at=NOW()
            WHERE id=:entry_id AND status='DRAFT'
        """), {"posted_by": posted_by, "entry_id": entry_id})
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Entry status changed. Refresh and try again.")
        db.commit()
        return get_entry(db, entry_id)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not post journal entry") from exc


@router.post("/{entry_id}/reverse", status_code=201)
def reverse_journal_entry(entry_id: int, reason: str = Query(..., min_length=3, max_length=255),
                          reversed_by: Optional[str] = Query(None, max_length=100),
                          db: Session = Depends(get_db)):
    original = get_entry(db, entry_id)
    if original["status"] != "POSTED":
        raise HTTPException(409, "Only Posted journal entries can be reversed")
    if original["reversal_of_id"] is not None:
        raise HTTPException(409, "A reversal entry cannot itself be reversed")

    year, period = get_fiscal_context(
        db, original["company_id"], original["fiscal_year_id"],
        original["fiscal_period_id"], original["entry_date"], original["posting_date"]
    )
    if str(year["status"]).lower() != "open" or str(period["status"]).lower() != "open":
        raise HTTPException(409, "The original fiscal year and period must be Open to reverse this entry")

    try:
        result = db.execute(text("""
            INSERT INTO fico_journal_entries
                (company_id, company_code, fiscal_year_id, fiscal_period_id,
                 entry_date, posting_date, currency, reference_number, description,
                 total_debit, total_credit, status, entry_type, reversal_of_id,
                 created_by, posted_by, posted_at)
            VALUES
                (:company_id, :company_code, :fiscal_year_id, :fiscal_period_id,
                 :entry_date, :posting_date, :currency, :reference_number, :description,
                 :total_debit, :total_credit, 'POSTED', 'REVERSAL', :reversal_of_id,
                 :created_by, :posted_by, NOW())
        """), {
            "company_id": original["company_id"],
            "company_code": original["company_code"],
            "fiscal_year_id": original["fiscal_year_id"],
            "fiscal_period_id": original["fiscal_period_id"],
            "entry_date": original["posting_date"],
            "posting_date": original["posting_date"],
            "currency": original["currency"],
            "reference_number": original["entry_number"],
            "description": f"Reversal of {original['entry_number']}: {reason}",
            "total_debit": original["total_credit"],
            "total_credit": original["total_debit"],
            "reversal_of_id": entry_id,
            "created_by": reversed_by,
            "posted_by": reversed_by
        })
        reversal_id = result.lastrowid
        reversal_number = f"JE-{original['company_code']}-{reversal_id:08d}"
        db.execute(text("""
            UPDATE fico_journal_entries SET entry_number=:number WHERE id=:id
        """), {"number": reversal_number, "id": reversal_id})

        for line in original["lines"]:
            db.execute(text("""
                INSERT INTO fico_journal_entry_lines
                    (journal_entry_id, line_number, account_id, account_code,
                     account_name, line_description, debit, credit, cost_center)
                VALUES
                    (:entry_id, :line_number, :account_id, :account_code,
                     :account_name, :line_description, :debit, :credit, :cost_center)
            """), {
                "entry_id": reversal_id,
                "line_number": line["line_number"],
                "account_id": line["account_id"],
                "account_code": line["account_code"],
                "account_name": line["account_name"],
                "line_description": f"Reversal: {line['line_description'] or original['description']}",
                "debit": line["credit"],
                "credit": line["debit"],
                "cost_center": line["cost_center"]
            })

        db.execute(text("""
            UPDATE fico_journal_entries
            SET status='REVERSED'
            WHERE id=:id AND status='POSTED'
        """), {"id": entry_id})
        db.commit()
        return get_entry(db, reversal_id)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This journal entry has already been reversed") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not reverse journal entry") from exc



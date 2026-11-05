"""STACKLY OneERP FICO - Fixed Assets module.

Uses existing database.py (get_db, engine) and existing tables:
  company(id, company_code)
  chart_of_accounts(id, company_code, account_code, account_name, account_type,
                    normal_balance, is_active)
  fico_fiscal_years(id, company_id, start_date, end_date, status)
  fico_fiscal_periods(id, fiscal_year_id, company_id, start_date, end_date, status)
  fico_journal_entries / fico_journal_entry_lines (same schema as journal_entry.py)

Creates only fixed-asset-specific tables. Accounting actions create DRAFT journal
entries in the existing journal tables. Users must post them through the existing
Journal Entry module; this keeps one accounting source of truth and lets the GL
pick up posted entries.

Register in main.py:
  from fixed_assets import router as fixed_assets_router
  app.include_router(fixed_assets_router)
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

try:
    from database import get_db, engine
except ImportError:  # package execution
    from .database import get_db, engine

router = APIRouter(prefix="/api/fixed-assets", tags=["Fixed Assets"])
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_MONEY = Decimal("9999999999999.99")
METHODS = {"STRAIGHT_LINE", "DECLINING_BALANCE"}


def dec(value) -> Decimal:
    try:
        out = Decimal(str(value if value is not None else 0)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422, "Invalid monetary amount")
    if not out.is_finite() or out < 0 or out > MAX_MONEY:
        raise HTTPException(422, "Amount is outside the supported range")
    return out


def ensure_tables():
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_asset_categories (
            id INT AUTO_INCREMENT PRIMARY KEY,
            company_id INT NOT NULL,
            category_code VARCHAR(30) NOT NULL,
            category_name VARCHAR(100) NOT NULL,
            description VARCHAR(255) NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_fa_category_company_code(company_id, category_code),
            INDEX ix_fa_category_company(company_id)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_fixed_assets (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            company_id INT NOT NULL,
            company_code VARCHAR(30) NOT NULL,
            category_id INT NOT NULL,
            asset_code VARCHAR(40) NOT NULL,
            asset_name VARCHAR(150) NOT NULL,
            description VARCHAR(500) NULL,
            serial_number VARCHAR(100) NULL,
            location VARCHAR(150) NULL,
            vendor_name VARCHAR(150) NULL,
            invoice_number VARCHAR(80) NULL,
            acquisition_date DATE NOT NULL,
            in_service_date DATE NOT NULL,
            acquisition_cost DECIMAL(15,2) NOT NULL,
            salvage_value DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            useful_life_months INT NOT NULL,
            depreciation_method VARCHAR(30) NOT NULL DEFAULT 'STRAIGHT_LINE',
            depreciation_rate DECIMAL(7,4) NULL,
            asset_account_id INT NOT NULL,
            accumulated_depreciation_account_id INT NOT NULL,
            depreciation_expense_account_id INT NOT NULL,
            clearing_account_id INT NOT NULL,
            acquisition_journal_id BIGINT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING_POSTING',
            disposal_date DATE NULL,
            disposal_proceeds DECIMAL(15,2) NULL,
            disposal_journal_id BIGINT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_fa_asset_company_code(company_id, asset_code),
            INDEX ix_fa_asset_company_status(company_id, status),
            INDEX ix_fa_asset_category(category_id)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_asset_depreciation_runs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            company_id INT NOT NULL,
            asset_id BIGINT NOT NULL,
            fiscal_year_id INT NOT NULL,
            fiscal_period_id INT NOT NULL,
            depreciation_date DATE NOT NULL,
            depreciation_amount DECIMAL(15,2) NOT NULL,
            accumulated_amount DECIMAL(15,2) NOT NULL,
            net_book_value DECIMAL(15,2) NOT NULL,
            journal_entry_id BIGINT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING_POSTING',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_fa_depreciation_asset_period(asset_id, fiscal_period_id),
            INDEX ix_fa_depr_company_period(company_id, fiscal_period_id)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_asset_events (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            company_id INT NOT NULL,
            asset_id BIGINT NOT NULL,
            event_type VARCHAR(30) NOT NULL,
            event_date DATE NOT NULL,
            description VARCHAR(255) NULL,
            old_value VARCHAR(255) NULL,
            new_value VARCHAR(255) NULL,
            journal_entry_id BIGINT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX ix_fa_events_asset(asset_id, event_date)
        ) ENGINE=InnoDB"""))


@router.on_event("startup")
def startup():
    ensure_tables()


def company_row(db: Session, company_id: int):
    row = db.execute(text("SELECT id, company_code FROM company WHERE id=:id LIMIT 1"),
                     {"id": company_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Company not found. Check the numeric Company ID.")
    return dict(row)


def account_row(db: Session, company_code: str, account_id: int):
    row = db.execute(text("""SELECT id, company_code, account_code, account_name,
                           account_type, normal_balance, is_active
                           FROM chart_of_accounts
                           WHERE id=:id AND company_code=:code LIMIT 1"""),
                     {"id": account_id, "code": company_code}).mappings().first()
    if not row:
        raise HTTPException(422, f"Account ID {account_id} does not belong to this company")
    if not bool(row["is_active"]):
        raise HTTPException(422, f"Account {row['account_code']} is inactive")
    return dict(row)


def fiscal_context(db: Session, company_id: int, year_id: int, period_id: int, tx_date: date):
    year = db.execute(text("""SELECT id, company_id, start_date, end_date, status
                            FROM fico_fiscal_years WHERE id=:y AND company_id=:c"""),
                      {"y": year_id, "c": company_id}).mappings().first()
    if not year:
        raise HTTPException(404, "Fiscal year not found for this company")
    period = db.execute(text("""SELECT id, company_id, fiscal_year_id, start_date, end_date, status
                              FROM fico_fiscal_periods
                              WHERE id=:p AND company_id=:c AND fiscal_year_id=:y"""),
                        {"p": period_id, "c": company_id, "y": year_id}).mappings().first()
    if not period:
        raise HTTPException(404, "Fiscal period not found for the selected fiscal year")
    if not (year["start_date"] <= tx_date <= year["end_date"]):
        raise HTTPException(422, "Transaction date must be within the selected fiscal year")
    if not (period["start_date"] <= tx_date <= period["end_date"]):
        raise HTTPException(422, "Transaction date must be within the selected fiscal period")
    if str(year["status"]).lower() != "open" or str(period["status"]).lower() != "open":
        raise HTTPException(409, "Fiscal year and fiscal period must both be Open")
    return dict(year), dict(period)


def category_row(db: Session, company_id: int, category_id: int):
    row = db.execute(text("""SELECT * FROM fico_asset_categories
                           WHERE id=:id AND company_id=:c AND is_active=1"""),
                     {"id": category_id, "c": company_id}).mappings().first()
    if not row:
        raise HTTPException(422, "Active asset category not found for this company")
    return dict(row)


def insert_journal(db: Session, company: dict, year_id: int, period_id: int,
                   tx_date: date, description: str, reference: str,
                   lines: list[dict], created_by: Optional[str] = "Fixed Assets") -> int:
    """Insert a balanced DRAFT journal using the existing Journal Entry schema."""
    debit_total = sum((dec(x.get("debit")) for x in lines), ZERO)
    credit_total = sum((dec(x.get("credit")) for x in lines), ZERO)
    if debit_total <= ZERO or debit_total != credit_total:
        raise HTTPException(422, "Generated accounting entry is not balanced")
    for line in lines:
        if (dec(line.get("debit")) > ZERO) == (dec(line.get("credit")) > ZERO):
            raise HTTPException(422, "Each journal line must have either debit or credit")
        account_row(db, company["company_code"], int(line["account_id"]))
    result = db.execute(text("""INSERT INTO fico_journal_entries
        (company_id, company_code, fiscal_year_id, fiscal_period_id, entry_date,
         posting_date, currency, reference_number, description, total_debit,
         total_credit, status, entry_type, created_by)
        VALUES (:company_id,:company_code,:fy,:period,:tx_date,:tx_date,'INR',
                :reference,:description,:debit,:credit,'DRAFT','STANDARD',:created_by)"""), {
        "company_id": company["id"], "company_code": company["company_code"],
        "fy": year_id, "period": period_id, "tx_date": tx_date,
        "reference": reference, "description": description[:255],
        "debit": debit_total, "credit": credit_total, "created_by": created_by
    })
    jid = int(result.lastrowid)
    db.execute(text("UPDATE fico_journal_entries SET entry_number=:n WHERE id=:id"),
               {"n": f"JE-{company['company_code']}-{jid:08d}", "id": jid})
    for i, line in enumerate(lines, 1):
        acc = account_row(db, company["company_code"], int(line["account_id"]))
        db.execute(text("""INSERT INTO fico_journal_entry_lines
            (journal_entry_id,line_number,account_id,account_code,account_name,
             line_description,debit,credit,cost_center)
            VALUES (:jid,:ln,:aid,:acode,:aname,:descr,:debit,:credit,:cost)"""), {
            "jid": jid, "ln": i, "aid": acc["id"], "acode": acc["account_code"],
            "aname": acc["account_name"], "descr": line.get("description"),
            "debit": dec(line.get("debit")), "credit": dec(line.get("credit")),
            "cost": line.get("cost_center")
        })
    return jid


def asset_row(db: Session, asset_id: int, company_id: Optional[int] = None):
    sql = "SELECT * FROM fico_fixed_assets WHERE id=:id"
    args = {"id": asset_id}
    if company_id is not None:
        sql += " AND company_id=:company_id"
        args["company_id"] = company_id
    row = db.execute(text(sql), args).mappings().first()
    if not row:
        raise HTTPException(404, "Fixed asset not found")
    return dict(row)


def posted_depreciation(db: Session, asset_id: int) -> Decimal:
    row = db.execute(text("""SELECT COALESCE(SUM(r.depreciation_amount),0) total
        FROM fico_asset_depreciation_runs r
        JOIN fico_journal_entries j ON j.id=r.journal_entry_id
        WHERE r.asset_id=:id AND j.status IN ('POSTED','REVERSED')"""),
        {"id": asset_id}).mappings().one()
    # Reversal journals are retained in the journal but a reversed depreciation
    # source should not silently be treated as posted depreciation. For now only
    # POSTED source entries count; reversed source entries are handled separately.
    row2 = db.execute(text("""SELECT COALESCE(SUM(r.depreciation_amount),0) total
        FROM fico_asset_depreciation_runs r
        JOIN fico_journal_entries j ON j.id=r.journal_entry_id
        WHERE r.asset_id=:id AND j.status='POSTED'"""), {"id": asset_id}).mappings().one()
    return dec(row2["total"])


def book_values(db: Session, asset: dict):
    cost, salvage = dec(asset["acquisition_cost"]), dec(asset["salvage_value"])
    accumulated = posted_depreciation(db, int(asset["id"]))
    accumulated = min(accumulated, max(cost - salvage, ZERO))
    return accumulated, max(cost - accumulated, ZERO)


class CategoryCreate(BaseModel):
    company_id: int = Field(gt=0)
    category_code: str = Field(min_length=1, max_length=30)
    category_name: str = Field(min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=255)

    @field_validator("category_code", "category_name")
    @classmethod
    def clean_required(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Value cannot be empty")
        return v


class AssetCreate(BaseModel):
    company_id: int = Field(gt=0)
    category_id: int = Field(gt=0)
    asset_code: str = Field(min_length=1, max_length=40)
    asset_name: str = Field(min_length=2, max_length=150)
    description: Optional[str] = Field(None, max_length=500)
    serial_number: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=150)
    vendor_name: Optional[str] = Field(None, max_length=150)
    invoice_number: Optional[str] = Field(None, max_length=80)
    acquisition_date: date
    in_service_date: date
    acquisition_cost: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    salvage_value: Decimal = Field(default=ZERO, ge=0, max_digits=15, decimal_places=2)
    useful_life_months: int = Field(ge=1, le=1200)
    depreciation_method: Literal["STRAIGHT_LINE", "DECLINING_BALANCE"] = "STRAIGHT_LINE"
    depreciation_rate: Optional[Decimal] = Field(None, gt=0, le=100, max_digits=7, decimal_places=4)
    asset_account_id: int = Field(gt=0)
    accumulated_depreciation_account_id: int = Field(gt=0)
    depreciation_expense_account_id: int = Field(gt=0)
    clearing_account_id: int = Field(gt=0)
    fiscal_year_id: int = Field(gt=0)
    fiscal_period_id: int = Field(gt=0)

    @field_validator("asset_code", "asset_name")
    @classmethod
    def strip_required(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Value cannot be empty")
        return v

    @model_validator(mode="after")
    def validate_asset(self):
        if self.in_service_date < self.acquisition_date:
            raise ValueError("In-service date cannot be earlier than acquisition date")
        if dec(self.salvage_value) >= dec(self.acquisition_cost):
            raise ValueError("Salvage value must be less than acquisition cost")
        ids = [self.asset_account_id, self.accumulated_depreciation_account_id,
               self.depreciation_expense_account_id, self.clearing_account_id]
        if len(set(ids)) != 4:
            raise ValueError("Select four different accounts for asset, accumulated depreciation, expense and clearing")
        if self.depreciation_method == "DECLINING_BALANCE" and self.depreciation_rate is None:
            raise ValueError("Depreciation rate (%) is required for declining balance method")
        return self


class AssetUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=500)
    serial_number: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=150)
    vendor_name: Optional[str] = Field(None, max_length=150)
    invoice_number: Optional[str] = Field(None, max_length=80)


class DepreciationRequest(BaseModel):
    company_id: int = Field(gt=0)
    fiscal_year_id: int = Field(gt=0)
    fiscal_period_id: int = Field(gt=0)
    depreciation_date: date


class DisposalRequest(BaseModel):
    company_id: int = Field(gt=0)
    fiscal_year_id: int = Field(gt=0)
    fiscal_period_id: int = Field(gt=0)
    disposal_date: date
    proceeds: Decimal = Field(default=ZERO, ge=0, max_digits=15, decimal_places=2)
    proceeds_account_id: int = Field(gt=0)
    gain_account_id: int = Field(gt=0)
    loss_account_id: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=255)


class TransferRequest(BaseModel):
    company_id: int = Field(gt=0)
    transfer_date: date
    new_location: str = Field(min_length=1, max_length=150)
    reason: Optional[str] = Field(None, max_length=255)


@router.get("/health")
def health():
    return {"module": "Fixed Assets", "status": "ready"}


@router.get("/context")
def context(company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    company = company_row(db, company_id)
    years = db.execute(text("SELECT id,year_code,year_name,start_date,end_date,status FROM fico_fiscal_years WHERE company_id=:c ORDER BY start_date DESC"), {"c": company_id}).mappings().all()
    periods = db.execute(text("SELECT id,fiscal_year_id,period_number,period_name,start_date,end_date,status FROM fico_fiscal_periods WHERE company_id=:c ORDER BY start_date"), {"c": company_id}).mappings().all()
    accounts = db.execute(text("""SELECT id,account_code,account_name,account_type,normal_balance
        FROM chart_of_accounts WHERE company_code=:code AND is_active=1 ORDER BY account_code"""),
        {"code": company["company_code"]}).mappings().all()
    cats = db.execute(text("SELECT id,category_code,category_name FROM fico_asset_categories WHERE company_id=:c AND is_active=1 ORDER BY category_name"), {"c": company_id}).mappings().all()
    return {"company": company, "years": [dict(x) for x in years],
            "periods": [dict(x) for x in periods], "accounts": [dict(x) for x in accounts],
            "categories": [dict(x) for x in cats]}


@router.get("/categories")
def list_categories(company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    company_row(db, company_id)
    rows = db.execute(text("SELECT * FROM fico_asset_categories WHERE company_id=:c ORDER BY category_name"), {"c": company_id}).mappings().all()
    return [dict(x) for x in rows]


@router.post("/categories", status_code=201)
def create_category(x: CategoryCreate, db: Session = Depends(get_db)):
    company_row(db, x.company_id)
    try:
        r = db.execute(text("""INSERT INTO fico_asset_categories
            (company_id,category_code,category_name,description)
            VALUES (:company_id,:category_code,:category_name,:description)"""), x.model_dump())
        db.commit()
        return dict(db.execute(text("SELECT * FROM fico_asset_categories WHERE id=:id"), {"id": r.lastrowid}).mappings().one())
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Category code already exists for this company") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not create asset category") from exc


@router.get("/summary")
def summary(company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    company_row(db, company_id)
    rows = db.execute(text("SELECT * FROM fico_fixed_assets WHERE company_id=:c"), {"c": company_id}).mappings().all()
    total_cost = total_accum = total_nbv = ZERO
    for row in rows:
        a = dict(row)
        accum, nbv = book_values(db, a)
        total_cost += dec(a["acquisition_cost"])
        total_accum += accum
        total_nbv += nbv
    counts = {"total_assets": len(rows), "active_assets": sum(1 for x in rows if x["status"] == "ACTIVE"),
              "pending_posting": sum(1 for x in rows if x["status"] == "PENDING_POSTING"),
              "disposed_assets": sum(1 for x in rows if x["status"] == "DISPOSED")}
    return {**counts, "gross_cost": float(total_cost), "accumulated_depreciation": float(total_accum),
            "net_book_value": float(total_nbv)}


@router.get("/assets")
def list_assets(company_id: int = Query(gt=0), search: Optional[str] = None,
                status_filter: Optional[str] = Query(None, alias="status"),
                category_id: Optional[int] = Query(None, gt=0),
                limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db)):
    company_row(db, company_id)
    sql = "SELECT a.*, c.category_name FROM fico_fixed_assets a JOIN fico_asset_categories c ON c.id=a.category_id WHERE a.company_id=:c"
    args = {"c": company_id, "limit": limit, "offset": offset}
    if search and search.strip():
        sql += " AND (a.asset_code LIKE :s OR a.asset_name LIKE :s OR a.serial_number LIKE :s OR a.location LIKE :s)"
        args["s"] = f"%{search.strip()}%"
    if status_filter:
        sql += " AND a.status=:status"
        args["status"] = status_filter.upper()
    if category_id:
        sql += " AND a.category_id=:category_id"
        args["category_id"] = category_id
    sql += " ORDER BY a.id DESC LIMIT :limit OFFSET :offset"
    rows = db.execute(text(sql), args).mappings().all()
    result = []
    for row in rows:
        a = dict(row)
        accum, nbv = book_values(db, a)
        a["accumulated_depreciation"] = float(accum)
        a["net_book_value"] = float(nbv)
        for k in ("acquisition_cost", "salvage_value", "disposal_proceeds"):
            if a.get(k) is not None:
                a[k] = float(a[k])
        result.append(a)
    return result


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    a = asset_row(db, asset_id, company_id)
    accum, nbv = book_values(db, a)
    a["accumulated_depreciation"] = float(accum)
    a["net_book_value"] = float(nbv)
    events = db.execute(text("SELECT * FROM fico_asset_events WHERE asset_id=:id ORDER BY event_date DESC,id DESC"), {"id": asset_id}).mappings().all()
    a["events"] = [dict(x) for x in events]
    return a


@router.post("/assets", status_code=201)
def create_asset(x: AssetCreate, db: Session = Depends(get_db)):
    company = company_row(db, x.company_id)
    category_row(db, x.company_id, x.category_id)
    fiscal_context(db, x.company_id, x.fiscal_year_id, x.fiscal_period_id, x.acquisition_date)
    if x.in_service_date < x.acquisition_date:
        raise HTTPException(422, "In-service date cannot be earlier than acquisition date")
    account_ids = [x.asset_account_id, x.accumulated_depreciation_account_id,
                   x.depreciation_expense_account_id, x.clearing_account_id]
    for aid in account_ids:
        account_row(db, company["company_code"], aid)
    if len(set(account_ids)) != 4:
        raise HTTPException(422, "Asset, accumulated depreciation, expense and clearing accounts must be different")
    cost, salvage = dec(x.acquisition_cost), dec(x.salvage_value)
    if salvage >= cost:
        raise HTTPException(422, "Salvage value must be less than acquisition cost")
    try:
        r = db.execute(text("""INSERT INTO fico_fixed_assets
            (company_id,company_code,category_id,asset_code,asset_name,description,
             serial_number,location,vendor_name,invoice_number,acquisition_date,
             in_service_date,acquisition_cost,salvage_value,useful_life_months,
             depreciation_method,depreciation_rate,asset_account_id,
             accumulated_depreciation_account_id,depreciation_expense_account_id,
             clearing_account_id,status)
            VALUES (:company_id,:company_code,:category_id,:asset_code,:asset_name,:description,
             :serial_number,:location,:vendor_name,:invoice_number,:acquisition_date,
             :in_service_date,:cost,:salvage,:life,:method,:rate,:asset_account_id,
             :accum_account,:expense_account,:clearing_account,'PENDING_POSTING')"""), {
            "company_id": x.company_id, "company_code": company["company_code"],
            "category_id": x.category_id, "asset_code": x.asset_code,
            "asset_name": x.asset_name, "description": x.description,
            "serial_number": x.serial_number, "location": x.location,
            "vendor_name": x.vendor_name, "invoice_number": x.invoice_number,
            "acquisition_date": x.acquisition_date, "in_service_date": x.in_service_date,
            "cost": cost, "salvage": salvage, "life": x.useful_life_months,
            "method": x.depreciation_method, "rate": x.depreciation_rate,
            "asset_account_id": x.asset_account_id,
            "accum_account": x.accumulated_depreciation_account_id,
            "expense_account": x.depreciation_expense_account_id,
            "clearing_account": x.clearing_account_id
        })
        aid = int(r.lastrowid)
        journal_id = insert_journal(db, company, x.fiscal_year_id, x.fiscal_period_id,
            x.acquisition_date, f"Fixed asset acquisition - {x.asset_code}", x.invoice_number or x.asset_code,
            [{"account_id": x.asset_account_id, "debit": cost, "credit": ZERO, "description": f"Acquisition of {x.asset_name}"},
             {"account_id": x.clearing_account_id, "debit": ZERO, "credit": cost, "description": f"Asset acquisition clearing - {x.asset_code}"}])
        db.execute(text("UPDATE fico_fixed_assets SET acquisition_journal_id=:j WHERE id=:id"), {"j": journal_id, "id": aid})
        db.execute(text("""INSERT INTO fico_asset_events(company_id,asset_id,event_type,event_date,description,journal_entry_id)
            VALUES (:c,:a,'ACQUISITION',:d,:desc,:j)"""), {"c": x.company_id, "a": aid, "d": x.acquisition_date, "desc": "Acquisition journal created as Draft", "j": journal_id})
        db.commit()
        return {"asset": asset_row(db, aid), "journal_entry_id": journal_id,
                "journal_status": "DRAFT", "message": "Asset saved. Post its acquisition journal from the Journal Entry module to activate the asset."}
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Asset code already exists for this company") from exc
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not create asset and acquisition journal. Check existing journal table schema.") from exc


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, x: AssetUpdate, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    a = asset_row(db, asset_id, company_id)
    if a["status"] == "DISPOSED":
        raise HTTPException(409, "Disposed assets cannot be edited")
    changes = x.model_dump(exclude_unset=True)
    if not changes:
        return a
    allowed = {"description", "serial_number", "location", "vendor_name", "invoice_number"}
    sets = ",".join(f"{k}=:{k}" for k in changes if k in allowed)
    changes.update({"id": asset_id, "company_id": company_id})
    try:
        db.execute(text(f"UPDATE fico_fixed_assets SET {sets} WHERE id=:id AND company_id=:company_id"), changes)
        db.commit()
        return asset_row(db, asset_id, company_id)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not update asset") from exc


def depreciation_amount(asset: dict, accumulated: Decimal) -> Decimal:
    cost, salvage = dec(asset["acquisition_cost"]), dec(asset["salvage_value"])
    remaining = max(cost - salvage - accumulated, ZERO)
    if remaining <= ZERO:
        return ZERO
    if asset["depreciation_method"] == "STRAIGHT_LINE":
        amount = (cost - salvage) / Decimal(int(asset["useful_life_months"]))
    else:
        rate = Decimal(str(asset["depreciation_rate"] or 0)) / Decimal("100")
        # Rate is an annual percentage; convert to a monthly reducing-balance rate.
        monthly_rate = rate / Decimal("12")
        current_nbv = max(cost - accumulated, ZERO)
        amount = current_nbv * monthly_rate
    amount = min(dec(amount), remaining)
    return amount


@router.get("/assets/{asset_id}/depreciation-preview")
def depreciation_preview(asset_id: int, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    a = asset_row(db, asset_id, company_id)
    accum, nbv = book_values(db, a)
    amount = depreciation_amount(a, accum)
    return {"asset_id": asset_id, "asset_code": a["asset_code"],
            "method": a["depreciation_method"], "acquisition_cost": float(dec(a["acquisition_cost"])),
            "salvage_value": float(dec(a["salvage_value"])), "accumulated_depreciation": float(accum),
            "net_book_value": float(nbv), "next_period_depreciation": float(amount),
            "remaining_depreciable_value": float(max(dec(a["acquisition_cost"]) - dec(a["salvage_value"]) - accum, ZERO))}


@router.post("/assets/{asset_id}/depreciation", status_code=201)
def create_depreciation(asset_id: int, x: DepreciationRequest, db: Session = Depends(get_db)):
    a = asset_row(db, asset_id, x.company_id)
    if a["status"] not in {"ACTIVE", "FULLY_DEPRECIATED"}:
        # A pending acquisition journal must be posted first.
        if a["status"] == "PENDING_POSTING":
            je = db.execute(text("SELECT status FROM fico_journal_entries WHERE id=:id"), {"id": a["acquisition_journal_id"]}).scalar()
            if je != "POSTED":
                raise HTTPException(409, "Post the asset acquisition journal before depreciating this asset")
            db.execute(text("UPDATE fico_fixed_assets SET status='ACTIVE' WHERE id=:id"), {"id": asset_id})
            a["status"] = "ACTIVE"
        else:
            raise HTTPException(409, "Only active assets can be depreciated")
    company = company_row(db, x.company_id)
    fiscal_context(db, x.company_id, x.fiscal_year_id, x.fiscal_period_id, x.depreciation_date)
    if x.depreciation_date < a["in_service_date"]:
        raise HTTPException(422, "Depreciation date cannot be earlier than in-service date")
    existing = db.execute(text("SELECT id FROM fico_asset_depreciation_runs WHERE asset_id=:a AND fiscal_period_id=:p"),
                          {"a": asset_id, "p": x.fiscal_period_id}).first()
    if existing:
        raise HTTPException(409, "Depreciation has already been generated for this asset and fiscal period")
    accumulated, nbv = book_values(db, a)
    amount = depreciation_amount(a, accumulated)
    if amount <= ZERO:
        db.execute(text("UPDATE fico_fixed_assets SET status='FULLY_DEPRECIATED' WHERE id=:id"), {"id": asset_id})
        db.commit()
        raise HTTPException(409, "Asset has no remaining depreciable value")
    new_accum, new_nbv = accumulated + amount, nbv - amount
    try:
        journal_id = insert_journal(db, company, x.fiscal_year_id, x.fiscal_period_id,
            x.depreciation_date, f"Depreciation - {a['asset_code']}", f"FA-DEP-{asset_id}-{x.fiscal_period_id}",
            [{"account_id": a["depreciation_expense_account_id"], "debit": amount, "credit": ZERO, "description": f"Depreciation expense - {a['asset_name']}"},
             {"account_id": a["accumulated_depreciation_account_id"], "debit": ZERO, "credit": amount, "description": f"Accumulated depreciation - {a['asset_code']}"}])
        r = db.execute(text("""INSERT INTO fico_asset_depreciation_runs
            (company_id,asset_id,fiscal_year_id,fiscal_period_id,depreciation_date,
             depreciation_amount,accumulated_amount,net_book_value,journal_entry_id,status)
            VALUES (:c,:a,:fy,:p,:d,:amt,:acc,:nbv,:j,'PENDING_POSTING')"""), {
            "c": x.company_id, "a": asset_id, "fy": x.fiscal_year_id, "p": x.fiscal_period_id,
            "d": x.depreciation_date, "amt": amount, "acc": new_accum, "nbv": new_nbv, "j": journal_id
        })
        db.execute(text("""INSERT INTO fico_asset_events(company_id,asset_id,event_type,event_date,description,journal_entry_id)
            VALUES (:c,:a,'DEPRECIATION',:d,:desc,:j)"""), {"c": x.company_id, "a": asset_id, "d": x.depreciation_date, "desc": f"Depreciation {amount} generated as Draft", "j": journal_id})
        db.commit()
        return {"depreciation_run_id": int(r.lastrowid), "journal_entry_id": journal_id,
                "amount": float(amount), "accumulated_depreciation_after_posting": float(new_accum),
                "net_book_value_after_posting": float(new_nbv), "status": "PENDING_POSTING",
                "message": "Depreciation journal created as Draft. Post it in Journal Entry to update the GL and asset book value."}
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not generate depreciation journal") from exc


@router.get("/depreciation-runs")
def depreciation_runs(company_id: int = Query(gt=0), asset_id: Optional[int] = Query(None, gt=0), db: Session = Depends(get_db)):
    company_row(db, company_id)
    sql = """SELECT r.*, a.asset_code, a.asset_name, j.entry_number, j.status journal_status
        FROM fico_asset_depreciation_runs r JOIN fico_fixed_assets a ON a.id=r.asset_id
        LEFT JOIN fico_journal_entries j ON j.id=r.journal_entry_id WHERE r.company_id=:c"""
    args = {"c": company_id}
    if asset_id:
        sql += " AND r.asset_id=:a"
        args["a"] = asset_id
    sql += " ORDER BY r.depreciation_date DESC,r.id DESC"
    return [dict(x) for x in db.execute(text(sql), args).mappings().all()]


@router.post("/assets/{asset_id}/transfer")
def transfer_asset(asset_id: int, x: TransferRequest, db: Session = Depends(get_db)):
    a = asset_row(db, asset_id, x.company_id)
    if a["status"] == "DISPOSED":
        raise HTTPException(409, "Disposed asset cannot be transferred")
    new_location = x.new_location.strip()
    if not new_location:
        raise HTTPException(422, "New location is required")
    old_location = a.get("location") or ""
    try:
        db.execute(text("UPDATE fico_fixed_assets SET location=:loc WHERE id=:id AND company_id=:c"),
                   {"loc": new_location, "id": asset_id, "c": x.company_id})
        db.execute(text("""INSERT INTO fico_asset_events(company_id,asset_id,event_type,event_date,description,old_value,new_value)
            VALUES (:c,:a,'TRANSFER',:d,:desc,:old,:new)"""), {
            "c": x.company_id, "a": asset_id, "d": x.transfer_date,
            "desc": x.reason or "Asset location transfer", "old": old_location, "new": new_location
        })
        db.commit()
        return {"message": "Asset location updated", "old_location": old_location, "new_location": new_location}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not transfer asset") from exc


@router.post("/assets/{asset_id}/dispose", status_code=201)
def dispose_asset(asset_id: int, x: DisposalRequest, db: Session = Depends(get_db)):
    a = asset_row(db, asset_id, x.company_id)
    if a["status"] != "ACTIVE":
        raise HTTPException(409, "Only active assets can be disposed")
    company = company_row(db, x.company_id)
    fiscal_context(db, x.company_id, x.fiscal_year_id, x.fiscal_period_id, x.disposal_date)
    if x.disposal_date < a["in_service_date"]:
        raise HTTPException(422, "Disposal date cannot be earlier than in-service date")
    accum, nbv = book_values(db, a)
    proceeds = dec(x.proceeds)
    # Remove gross asset cost and accumulated depreciation; recognize gain or loss.
    lines = []
    if accum > ZERO:
        lines.append({"account_id": a["accumulated_depreciation_account_id"], "debit": accum, "credit": ZERO, "description": "Clear accumulated depreciation"})
    if proceeds > ZERO:
        lines.append({"account_id": x.proceeds_account_id, "debit": proceeds, "credit": ZERO, "description": "Disposal proceeds"})
    cost = dec(a["acquisition_cost"])
    if proceeds > nbv:
        gain = proceeds - nbv
        lines.append({"account_id": a["asset_account_id"], "debit": ZERO, "credit": cost, "description": "Remove asset cost"})
        lines.append({"account_id": x.gain_account_id, "debit": ZERO, "credit": gain, "description": "Gain on disposal"})
    elif proceeds < nbv:
        loss = nbv - proceeds
        lines.append({"account_id": x.loss_account_id, "debit": loss, "credit": ZERO, "description": "Loss on disposal"})
        lines.append({"account_id": a["asset_account_id"], "debit": ZERO, "credit": cost, "description": "Remove asset cost"})
    else:
        lines.append({"account_id": a["asset_account_id"], "debit": ZERO, "credit": cost, "description": "Remove asset cost"})
    journal_id = insert_journal(db, company, x.fiscal_year_id, x.fiscal_period_id,
        x.disposal_date, f"Fixed asset disposal - {a['asset_code']}", f"FA-DISP-{asset_id}", lines)
    try:
        db.execute(text("""UPDATE fico_fixed_assets SET disposal_date=:d,disposal_proceeds=:p,
            disposal_journal_id=:j,status='PENDING_DISPOSAL_POSTING' WHERE id=:id AND company_id=:c"""),
            {"d": x.disposal_date, "p": proceeds, "j": journal_id, "id": asset_id, "c": x.company_id})
        db.execute(text("""INSERT INTO fico_asset_events(company_id,asset_id,event_type,event_date,description,journal_entry_id)
            VALUES (:c,:a,'DISPOSAL',:d,:desc,:j)"""), {"c": x.company_id, "a": asset_id, "d": x.disposal_date,
            "desc": x.reason, "j": journal_id})
        db.commit()
        return {"journal_entry_id": journal_id, "net_book_value": float(nbv),
                "proceeds": float(proceeds), "gain": float(max(proceeds - nbv, ZERO)),
                "loss": float(max(nbv - proceeds, ZERO)), "status": "PENDING_POSTING",
                "message": "Disposal journal created as Draft. Post it in Journal Entry to complete disposal."}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(500, "Could not save asset disposal") from exc


@router.post("/sync-status")
def sync_status(company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    """Refresh asset lifecycle labels from linked journal-entry posting states."""
    company_row(db, company_id)
    assets = db.execute(text("SELECT * FROM fico_fixed_assets WHERE company_id=:c"), {"c": company_id}).mappings().all()
    changed = 0
    for raw in assets:
        a = dict(raw)
        if a["status"] == "PENDING_POSTING" and a.get("acquisition_journal_id"):
            state = db.execute(text("SELECT status FROM fico_journal_entries WHERE id=:id"), {"id": a["acquisition_journal_id"]}).scalar()
            if state == "POSTED":
                db.execute(text("UPDATE fico_fixed_assets SET status='ACTIVE' WHERE id=:id"), {"id": a["id"]})
                changed += 1
        if a["status"] == "PENDING_DISPOSAL_POSTING" and a.get("disposal_journal_id"):
            state = db.execute(text("SELECT status FROM fico_journal_entries WHERE id=:id"), {"id": a["disposal_journal_id"]}).scalar()
            if state == "POSTED":
                db.execute(text("UPDATE fico_fixed_assets SET status='DISPOSED' WHERE id=:id"), {"id": a["id"]})
                changed += 1
    db.commit()
    return {"message": "Asset statuses synchronized", "updated_assets": changed}

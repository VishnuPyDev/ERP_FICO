"""
Accounts Payable (AP) module for STACKLY OneERP FICO.

Single-file FastAPI module: vendor management, supplier invoices, payments,
invoice balances, and AP metrics.

Integration:
    from ap import router as ap_router
    app.include_router(ap_router)

By default, this module expects an existing SQLAlchemy Session dependency in
backend.database.get_db and an existing SQLAlchemy Company model in
backend.models (or models). Adjust the two import lines below to match your
project's actual package layout. This module creates AP tables if missing.
It does NOT create or modify your Company table.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String,
    Text, UniqueConstraint, func, or_
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, relationship

# Adjust these imports only if your project uses a package (e.g. from .database).
try:
    from database import get_db
except ImportError:
    from .database import get_db

# AP tables use the same SQLAlchemy Base as your existing models when possible.
try:
    from models import Base as ExistingBase, Company
    Base = ExistingBase
except (ImportError, AttributeError):
    try:
        from .models import Base as ExistingBase, Company
        Base = ExistingBase
    except (ImportError, AttributeError):
        # Fallback Base is safe only if your existing project has no shared Base.
        Base = declarative_base()
        Company = None

router = APIRouter(prefix="/api/ap", tags=["Accounts Payable"])


# ----------------------------- Database models -----------------------------
class APVendor(Base):
    __tablename__ = "ap_vendors"
    __table_args__ = (UniqueConstraint("company_id", "vendor_code", name="uq_ap_vendor_company_code"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    vendor_code = Column(String(30), nullable=False)
    vendor_name = Column(String(160), nullable=False, index=True)
    email = Column(String(254), nullable=True)
    phone = Column(String(40), nullable=True)
    tax_id = Column(String(60), nullable=True)
    address = Column(Text, nullable=True)
    payment_terms_days = Column(Integer, nullable=False, default=30)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    invoices = relationship("APInvoice", back_populates="vendor")


class APInvoice(Base):
    __tablename__ = "ap_invoices"
    __table_args__ = (UniqueConstraint("company_id", "vendor_id", "invoice_number", name="uq_ap_invoice_vendor_number"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    vendor_id = Column(Integer, ForeignKey("ap_vendors.id"), nullable=False, index=True)
    invoice_number = Column(String(60), nullable=False)
    invoice_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False, index=True)
    currency = Column(String(3), nullable=False, default="INR")
    subtotal = Column(Numeric(14, 2), nullable=False)
    tax_amount = Column(Numeric(14, 2), nullable=False, default=0)
    total_amount = Column(Numeric(14, 2), nullable=False)
    paid_amount = Column(Numeric(14, 2), nullable=False, default=0)
    description = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="Draft", index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    vendor = relationship("APVendor", back_populates="invoices")
    payments = relationship("APPayment", back_populates="invoice", cascade="all, delete-orphan")

    @property
    def balance_due(self):
        return max(Decimal("0.00"), Decimal(self.total_amount or 0) - Decimal(self.paid_amount or 0))


class APPayment(Base):
    __tablename__ = "ap_payments"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("ap_invoices.id"), nullable=False, index=True)
    payment_date = Column(Date, nullable=False, default=date.today)
    amount = Column(Numeric(14, 2), nullable=False)
    payment_method = Column(String(30), nullable=False, default="Bank Transfer")
    payment_reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="Completed")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    invoice = relationship("APInvoice", back_populates="payments")


# ------------------------------- API schemas -------------------------------
class VendorCreate(BaseModel):
    company_id: int = Field(gt=0)
    vendor_code: str = Field(min_length=1, max_length=30)
    vendor_name: str = Field(min_length=1, max_length=160)
    email: Optional[str] = Field(default=None, max_length=254)
    phone: Optional[str] = Field(default=None, max_length=40)
    tax_id: Optional[str] = Field(default=None, max_length=60)
    address: Optional[str] = None
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    model_config = ConfigDict(str_strip_whitespace=True)


class VendorUpdate(BaseModel):
    vendor_name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    email: Optional[str] = Field(default=None, max_length=254)
    phone: Optional[str] = Field(default=None, max_length=40)
    tax_id: Optional[str] = Field(default=None, max_length=60)
    address: Optional[str] = None
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    is_active: Optional[bool] = None
    model_config = ConfigDict(str_strip_whitespace=True)


class InvoiceCreate(BaseModel):
    company_id: int = Field(gt=0)
    vendor_id: int = Field(gt=0)
    invoice_number: str = Field(min_length=1, max_length=60)
    invoice_date: date
    due_date: date
    currency: str = Field(default="INR", min_length=3, max_length=3)
    subtotal: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=14, decimal_places=2)
    description: Optional[str] = None
    status: str = Field(default="Draft")
    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value):
        return value.upper()

    @field_validator("invoice_number")
    @classmethod
    def nonblank_invoice(cls, value):
        if not value.strip():
            raise ValueError("Invoice number cannot be blank")
        return value.strip()

    @field_validator("status")
    @classmethod
    def valid_initial_status(cls, value):
        # Approval/rejection must happen through the dedicated status endpoint.
        allowed = {"Draft", "Pending"}
        if value not in allowed:
            raise ValueError("New invoices must start with Draft or Pending status")
        return value

    @model_validator(mode="after")
    def validate_invoice_dates(self):
        if self.due_date < self.invoice_date:
            raise ValueError("Due date cannot be earlier than invoice date")
        return self


class InvoiceUpdate(BaseModel):
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    subtotal: Optional[Decimal] = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    tax_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    description: Optional[str] = None

    @field_validator("currency")
    @classmethod
    def uppercase_optional_currency(cls, value):
        return value.upper() if value else value


class InvoiceStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, value):
        allowed = {"Draft", "Pending", "Approved", "Rejected", "Cancelled"}
        if value not in allowed:
            raise ValueError(f"Status must be one of: {', '.join(sorted(allowed))}")
        return value


class PaymentCreate(BaseModel):
    company_id: int = Field(gt=0)
    payment_date: date = Field(default_factory=date.today)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_method: str = Field(default="Bank Transfer", min_length=1, max_length=30)
    payment_reference: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None


# -------------------------------- Helpers ----------------------------------
def _ensure_tables():
    """Create AP tables if missing; uses shared Base when available."""
    # Create only AP tables, never the entire existing schema.
    engine = None
    try:
        from database import engine as db_engine
        engine = db_engine
    except ImportError:
        try:
            from .database import engine as db_engine
            engine = db_engine
        except ImportError:
            pass
    if engine is not None:
        Base.metadata.create_all(bind=engine, tables=[
            APVendor.__table__, APInvoice.__table__, APPayment.__table__
        ])


def _vendor_or_404(db: Session, company_id: int, vendor_id: int):
    row = db.query(APVendor).filter(APVendor.id == vendor_id, APVendor.company_id == company_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Vendor not found for this company")
    return row


def _invoice_or_404(db: Session, company_id: int, invoice_id: int):
    row = db.query(APInvoice).filter(APInvoice.id == invoice_id, APInvoice.company_id == company_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Invoice not found for this company")
    return row


def _invoice_status(invoice: APInvoice):
    if invoice.status in {"Draft", "Pending", "Rejected", "Cancelled"}:
        return invoice.status
    paid = Decimal(invoice.paid_amount or 0)
    total = Decimal(invoice.total_amount or 0)
    if paid >= total:
        return "Paid"
    if paid > 0:
        return "Partially Paid"
    if invoice.due_date < date.today():
        return "Overdue"
    return invoice.status or "Approved"


def _vendor_out(row: APVendor):
    return {
        "id": row.id, "company_id": row.company_id, "vendor_code": row.vendor_code,
        "vendor_name": row.vendor_name, "email": row.email, "phone": row.phone,
        "tax_id": row.tax_id, "address": row.address, "payment_terms_days": row.payment_terms_days,
        "is_active": row.is_active, "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _invoice_out(row: APInvoice):
    paid = Decimal(row.paid_amount or 0)
    total = Decimal(row.total_amount or 0)
    return {
        "id": row.id, "company_id": row.company_id, "vendor_id": row.vendor_id,
        "vendor_code": row.vendor.vendor_code if row.vendor else None,
        "vendor_name": row.vendor.vendor_name if row.vendor else None,
        "invoice_number": row.invoice_number, "invoice_date": row.invoice_date,
        "due_date": row.due_date, "currency": row.currency, "subtotal": total - Decimal(row.tax_amount or 0),
        "tax_amount": row.tax_amount, "total_amount": total, "paid_amount": paid,
        "balance_due": max(Decimal("0.00"), total - paid), "description": row.description,
        "status": _invoice_status(row), "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _payment_out(row: APPayment):
    invoice = row.invoice
    return {
        "id": row.id, "company_id": row.company_id, "invoice_id": row.invoice_id,
        "invoice_number": invoice.invoice_number if invoice else None,
        "vendor_id": invoice.vendor_id if invoice else None,
        "vendor_name": invoice.vendor.vendor_name if invoice and invoice.vendor else None,
        "payment_date": row.payment_date, "amount": row.amount, "payment_method": row.payment_method,
        "payment_reference": row.payment_reference, "notes": row.notes, "status": row.status,
        "created_at": row.created_at,
    }


@router.on_event("startup")
def create_ap_tables():
    try:
        _ensure_tables()
    except Exception as exc:
        # Don't hide startup error; clear message makes setup issue diagnosable.
        raise RuntimeError(f"Could not initialize Accounts Payable tables: {exc}") from exc


# -------------------------------- Vendors ----------------------------------
@router.post("/vendors", status_code=status.HTTP_201_CREATED)
def create_vendor(payload: VendorCreate, db: Session = Depends(get_db)):
    code = payload.vendor_code.strip().upper()
    if db.query(APVendor).filter(APVendor.company_id == payload.company_id, APVendor.vendor_code == code).first():
        raise HTTPException(status_code=409, detail="Vendor code already exists for this company")
    if Company is not None and not db.query(Company).filter(Company.id == payload.company_id).first():
        raise HTTPException(status_code=404, detail="Company not found")
    row = APVendor(company_id=payload.company_id, vendor_code=code, vendor_name=payload.vendor_name.strip(),
        email=payload.email, phone=payload.phone, tax_id=payload.tax_id, address=payload.address,
        payment_terms_days=payload.payment_terms_days, is_active=True)
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Vendor code already exists or data violates a database constraint")
    return _vendor_out(row)


@router.get("/vendors")
def list_vendors(company_id: int = Query(gt=0), search: Optional[str] = None,
                 active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(APVendor).filter(APVendor.company_id == company_id)
    if active_only:
        q = q.filter(APVendor.is_active.is_(True))
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(or_(APVendor.vendor_code.ilike(term), APVendor.vendor_name.ilike(term), APVendor.email.ilike(term)))
    return [_vendor_out(v) for v in q.order_by(APVendor.vendor_name).all()]


@router.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: int, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    return _vendor_out(_vendor_or_404(db, company_id, vendor_id))


@router.put("/vendors/{vendor_id}")
def update_vendor(vendor_id: int, payload: VendorUpdate, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    row = _vendor_or_404(db, company_id, vendor_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    try:
        db.commit()
        db.refresh(row)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Could not update vendor")
    return _vendor_out(row)


# -------------------------------- Invoices ---------------------------------
@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db)):
    vendor = _vendor_or_404(db, payload.company_id, payload.vendor_id)
    if not vendor.is_active:
        raise HTTPException(status_code=400, detail="Cannot create an invoice for an inactive vendor")
    duplicate = db.query(APInvoice).filter(
        APInvoice.company_id == payload.company_id, APInvoice.vendor_id == payload.vendor_id,
        APInvoice.invoice_number == payload.invoice_number.strip()
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="Invoice number already exists for this vendor")
    total = payload.subtotal + payload.tax_amount
    row = APInvoice(company_id=payload.company_id, vendor_id=payload.vendor_id,
        invoice_number=payload.invoice_number.strip(), invoice_date=payload.invoice_date,
        due_date=payload.due_date, currency=payload.currency, subtotal=payload.subtotal,
        tax_amount=payload.tax_amount, total_amount=total, paid_amount=Decimal("0.00"),
        description=payload.description, status=payload.status)
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate invoice or invalid vendor/company")
    return _invoice_out(row)


@router.get("/invoices")
def list_invoices(company_id: int = Query(gt=0), search: Optional[str] = None,
                  invoice_status: Optional[str] = Query(default=None, alias="status"),
                  vendor_id: Optional[int] = Query(default=None, gt=0),
                  from_date: Optional[date] = None, to_date: Optional[date] = None,
                  db: Session = Depends(get_db)):
    q = db.query(APInvoice).filter(APInvoice.company_id == company_id)
    if search:
        term = f"%{search.strip()}%"
        q = q.join(APVendor).filter(or_(APInvoice.invoice_number.ilike(term), APVendor.vendor_name.ilike(term), APVendor.vendor_code.ilike(term)))
    if invoice_status:
        if invoice_status == "Overdue":
            q = q.filter(APInvoice.due_date < date.today(), APInvoice.paid_amount < APInvoice.total_amount)
        elif invoice_status == "Paid":
            q = q.filter(APInvoice.paid_amount >= APInvoice.total_amount)
        else:
            q = q.filter(APInvoice.status == invoice_status)
    if vendor_id:
        q = q.filter(APInvoice.vendor_id == vendor_id)
    if from_date:
        q = q.filter(APInvoice.invoice_date >= from_date)
    if to_date:
        q = q.filter(APInvoice.invoice_date <= to_date)
    return [_invoice_out(i) for i in q.order_by(APInvoice.invoice_date.desc(), APInvoice.id.desc()).all()]


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    return _invoice_out(_invoice_or_404(db, company_id, invoice_id))


@router.put("/invoices/{invoice_id}")
def update_invoice(invoice_id: int, payload: InvoiceUpdate, company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    row = _invoice_or_404(db, company_id, invoice_id)
    if Decimal(row.paid_amount or 0) > 0 and (payload.subtotal is not None or payload.tax_amount is not None):
        raise HTTPException(status_code=409, detail="Cannot change invoice amount after payments have been recorded")
    data = payload.model_dump(exclude_unset=True)

    proposed_invoice_date = data.get("invoice_date", row.invoice_date)
    proposed_due_date = data.get("due_date", row.due_date)
    if proposed_due_date < proposed_invoice_date:
        raise HTTPException(status_code=422, detail="Due date cannot be earlier than invoice date")

    for key, value in data.items():
        setattr(row, key, value)
    if payload.subtotal is not None or payload.tax_amount is not None:
        subtotal = Decimal(row.subtotal or 0)
        tax = Decimal(row.tax_amount or 0)
        row.total_amount = subtotal + tax
    try:
        db.commit()
        db.refresh(row)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Could not update invoice")
    return _invoice_out(row)


@router.put("/invoices/{invoice_id}/status")
def update_invoice_status(invoice_id: int, payload: InvoiceStatusUpdate,
                          company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    row = _invoice_or_404(db, company_id, invoice_id)
    if payload.status in {"Rejected", "Cancelled"} and Decimal(row.paid_amount or 0) > 0:
        raise HTTPException(status_code=409, detail="Cannot reject/cancel an invoice that has payments")
    row.status = payload.status
    try:
        db.commit()
        db.refresh(row)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Could not update invoice status")
    return _invoice_out(row)


# -------------------------------- Payments ---------------------------------
@router.post("/invoices/{invoice_id}/payments", status_code=status.HTTP_201_CREATED)
def record_payment(invoice_id: int, payload: PaymentCreate, company_id: int = Query(gt=0),
                   db: Session = Depends(get_db)):
    invoice = _invoice_or_404(db, company_id, invoice_id)
    if invoice.status not in {"Approved", "Partially Paid"} and Decimal(invoice.paid_amount or 0) == 0:
        raise HTTPException(status_code=409, detail="Invoice must be approved before payment")
    total = Decimal(invoice.total_amount or 0)
    paid = Decimal(invoice.paid_amount or 0)
    if payload.amount > total - paid:
        raise HTTPException(status_code=400, detail="Payment exceeds the outstanding invoice balance")
    payment = APPayment(company_id=company_id, invoice_id=invoice.id, payment_date=payload.payment_date,
        amount=payload.amount, payment_method=payload.payment_method, payment_reference=payload.payment_reference,
        notes=payload.notes, status="Completed")
    invoice.paid_amount = paid + payload.amount
    invoice.status = "Paid" if invoice.paid_amount >= total else "Partially Paid"
    db.add(payment)
    try:
        db.commit()
        db.refresh(payment)
        db.refresh(invoice)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Could not record payment")
    return {"payment": _payment_out(payment), "invoice": _invoice_out(invoice)}


@router.get("/payments")
def list_payments(company_id: int = Query(gt=0), search: Optional[str] = None,
                  from_date: Optional[date] = None, to_date: Optional[date] = None,
                  db: Session = Depends(get_db)):
    q = db.query(APPayment).filter(APPayment.company_id == company_id)
    if search:
        term = f"%{search.strip()}%"
        q = q.join(APInvoice).join(APVendor).filter(or_(
            APPayment.payment_reference.ilike(term), APInvoice.invoice_number.ilike(term),
            APVendor.vendor_name.ilike(term)
        ))
    if from_date:
        q = q.filter(APPayment.payment_date >= from_date)
    if to_date:
        q = q.filter(APPayment.payment_date <= to_date)
    return [_payment_out(p) for p in q.order_by(APPayment.payment_date.desc(), APPayment.id.desc()).all()]


# ---------------------------------- Metrics --------------------------------
@router.get("/metrics")
def ap_metrics(company_id: int = Query(gt=0), db: Session = Depends(get_db)):
    invoices = db.query(APInvoice).filter(APInvoice.company_id == company_id).all()
    payments = db.query(APPayment).filter(APPayment.company_id == company_id).all()
    open_invoices = [i for i in invoices if Decimal(i.total_amount or 0) > Decimal(i.paid_amount or 0)]
    overdue = [i for i in open_invoices if i.due_date < date.today()]
    pending = [i for i in invoices if i.status in {"Draft", "Pending"}]
    total_outstanding = sum((Decimal(i.total_amount or 0) - Decimal(i.paid_amount or 0) for i in open_invoices), Decimal("0.00"))
    month_start = date.today().replace(day=1)
    payments_this_month = sum((Decimal(p.amount or 0) for p in payments if p.payment_date >= month_start), Decimal("0.00"))
    total_invoiced = sum((Decimal(i.total_amount or 0) for i in invoices), Decimal("0.00"))
    total_paid = sum((Decimal(p.amount or 0) for p in payments), Decimal("0.00"))
    return {
        "company_id": company_id,
        "supplier_count": db.query(APVendor).filter(APVendor.company_id == company_id).count(),
        "invoice_count": len(invoices),
        "open_invoice_count": len(open_invoices),
        "overdue_invoice_count": len(overdue),
        "pending_approval_count": len(pending),
        "total_outstanding": total_outstanding,
        "payments_this_month": payments_this_month,
        "total_invoiced": total_invoiced,
        "total_paid": total_paid,
    }


@router.get("/health")
def ap_health():
    return {"module": "Accounts Payable", "status": "ready"}

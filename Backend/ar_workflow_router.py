"""
Optional AR workflow endpoint for payment tracking.

This router adds ONLY GET /ar/workflow/tracking.
Keep existing ar_customers.py, ar_invoices.py and ar_payments.py.
Do not create duplicate /ar/invoices or /ar/payments routes here.
"""
import os
from datetime import date
from decimal import Decimal

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# Load Backend/.env before reading DB settings.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
password = os.getenv("DB_PASSWORD")
if not password:
    raise RuntimeError("DB_PASSWORD is missing. Check Backend/.env.")

db_url = URL.create(
    "mysql+pymysql",
    username=os.getenv("DB_USER", "root"),
    password=password,
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "3306")),
    database=os.getenv("DB_NAME", "erp_fico"),
)
engine = create_engine(db_url, pool_pre_ping=True)

router = APIRouter(prefix="/ar/workflow", tags=["AR Workflow"])


@router.get("/tracking")
def payment_tracking(company_code: str = Query(..., min_length=1, max_length=20)):
    """Calculate total paid, remaining balance and payment status per invoice."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT i.id AS invoice_id, i.company_code, i.invoice_number,
                   i.customer_id, i.invoice_date, i.due_date,
                   i.status AS invoice_status, i.total_amount,
                   COALESCE(SUM(p.amount), 0) AS total_paid
            FROM ar_invoices AS i
            LEFT JOIN ar_payments AS p
              ON p.invoice_id = i.id AND p.company_code = i.company_code
            WHERE i.company_code = :company_code
              AND i.status <> 'CANCELLED'
            GROUP BY i.id, i.company_code, i.invoice_number, i.customer_id,
                     i.invoice_date, i.due_date, i.status, i.total_amount
            ORDER BY i.due_date ASC, i.id DESC
        """), {"company_code": company_code}).mappings().all()

    today = date.today()
    items = []
    for row in rows:
        total = Decimal(str(row["total_amount"] or 0)).quantize(Decimal("0.01"))
        paid = Decimal(str(row["total_paid"] or 0)).quantize(Decimal("0.01"))
        balance = max(Decimal("0.00"), total - paid)
        if balance == 0:
            status = "PAID"
        elif paid > 0:
            status = "PARTIALLY_PAID"
        elif row["due_date"] and row["due_date"] < today:
            status = "OVERDUE"
        else:
            status = "PENDING"
        item = dict(row)
        item.update({
            "total_amount": str(total),
            "total_paid": str(paid),
            "balance": str(balance),
            "payment_status": status,
        })
        # JSON-friendly dates
        for key in ("invoice_date", "due_date"):
            if item.get(key) is not None:
                item[key] = item[key].isoformat()
        items.append(item)
    return {"company_code": company_code, "count": len(items), "items": items}

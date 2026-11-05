from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary")
def dashboard_summary(
    company_code: str = Query(min_length=1, max_length=20),
    db: Session = Depends(get_db),
):
    """Counts for the selected company using the existing CoA and AR tables."""
    accounts = db.execute(
        text("""
            SELECT COUNT(*)
            FROM chart_of_accounts
            WHERE company_code = :company_code
        """),
        {"company_code": company_code},
    ).scalar() or 0

    active_accounts = db.execute(
        text("""
            SELECT COUNT(*)
            FROM chart_of_accounts
            WHERE company_code = :company_code AND is_active = TRUE
        """),
        {"company_code": company_code},
    ).scalar() or 0

    customers = db.execute(
        text("""
            SELECT COUNT(*)
            FROM ar_customers
            WHERE company_code = :company_code
        """),
        {"company_code": company_code},
    ).scalar() or 0

    active_customers = db.execute(
        text("""
            SELECT COUNT(*)
            FROM ar_customers
            WHERE company_code = :company_code AND is_active = TRUE
        """),
        {"company_code": company_code},
    ).scalar() or 0

    invoice_counts = db.execute(
        text("""
            SELECT
                COUNT(*) AS total_invoices,
                COALESCE(SUM(CASE WHEN status = 'POSTED' THEN 1 ELSE 0 END), 0) AS posted_invoices,
                COALESCE(SUM(CASE WHEN status = 'DRAFT' THEN 1 ELSE 0 END), 0) AS draft_invoices,
                COALESCE(SUM(CASE WHEN status = 'CANCELLED' THEN 1 ELSE 0 END), 0) AS cancelled_invoices,
                COALESCE(SUM(CASE WHEN status = 'POSTED' THEN total_amount ELSE 0 END), 0) AS posted_invoice_amount
            FROM ar_invoices
            WHERE company_code = :company_code
        """),
        {"company_code": company_code},
    ).mappings().one()

    payment_stats = db.execute(
        text("""
            SELECT
                COUNT(*) AS total_payments,
                COALESCE(SUM(amount), 0) AS total_payment_amount
            FROM ar_payments
            WHERE company_code = :company_code
        """),
        {"company_code": company_code},
    ).mappings().one()

    return {
        "company_code": company_code,
        "total_accounts": int(accounts),
        "active_accounts": int(active_accounts),
        "total_customers": int(customers),
        "active_customers": int(active_customers),
        "total_invoices": int(invoice_counts["total_invoices"]),
        "posted_invoices": int(invoice_counts["posted_invoices"]),
        "draft_invoices": int(invoice_counts["draft_invoices"]),
        "cancelled_invoices": int(invoice_counts["cancelled_invoices"]),
        "posted_invoice_amount": float(invoice_counts["posted_invoice_amount"] or 0),
        "total_payments": int(payment_stats["total_payments"]),
        "total_payment_amount": float(payment_stats["total_payment_amount"] or 0),
    }


@router.get("/recent-transactions")
def recent_transactions(
    company_code: str = Query(min_length=1, max_length=20),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Recent invoice and payment activity. Does not use journal-entry tables."""
    invoices = db.execute(
        text("""
            SELECT
                invoice_date AS activity_date,
                invoice_number AS document_number,
                'Accounts Receivable' AS module,
                CONCAT('Customer invoice #', invoice_number) AS description,
                total_amount AS amount,
                status AS status,
                created_at AS created_at,
                'invoice' AS record_type
            FROM ar_invoices
            WHERE company_code = :company_code
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        {"company_code": company_code, "limit": limit},
    ).mappings().all()

    payments = db.execute(
        text("""
            SELECT
                p.payment_date AS activity_date,
                p.payment_reference AS document_number,
                'AR Payment' AS module,
                CONCAT('Payment for invoice #', i.invoice_number) AS description,
                p.amount AS amount,
                'PAID' AS status,
                p.created_at AS created_at,
                'payment' AS record_type
            FROM ar_payments p
            JOIN ar_invoices i ON i.id = p.invoice_id
            WHERE p.company_code = :company_code
            ORDER BY p.created_at DESC
            LIMIT :limit
        """),
        {"company_code": company_code, "limit": limit},
    ).mappings().all()

    records = [dict(row) for row in invoices] + [dict(row) for row in payments]
    records.sort(
        key=lambda row: row["created_at"] or row["activity_date"],
        reverse=True,
    )

    output = []
    for row in records[:limit]:
        output.append({
            "date": row["activity_date"].isoformat() if row["activity_date"] else None,
            "document_number": row["document_number"],
            "module": row["module"],
            "description": row["description"],
            "amount": float(row["amount"] or 0),
            "status": row["status"],
            "record_type": row["record_type"],
        })
    return output


@router.get("/financial-summary")
def ar_financial_summary(
    company_code: str = Query(min_length=1, max_length=20),
    period: str = Query(default="year", pattern="^(year|month)$"),
    db: Session = Depends(get_db),
):
    """
    AR-only summary. These values are invoice totals and received payments,
    not company-wide income, expenses, or net profit.
    """
    today = date.today()
    if period == "month":
        start_date = today.replace(day=1)
    else:
        start_date = today.replace(month=1, day=1)

    invoice_data = db.execute(
        text("""
            SELECT
                COALESCE(SUM(total_amount), 0) AS invoice_total,
                COALESCE(SUM(subtotal), 0) AS subtotal_total,
                COALESCE(SUM(tax_amount), 0) AS tax_total,
                COUNT(*) AS invoice_count
            FROM ar_invoices
            WHERE company_code = :company_code
              AND status = 'POSTED'
              AND invoice_date >= :start_date
              AND invoice_date <= :end_date
        """),
        {
            "company_code": company_code,
            "start_date": start_date,
            "end_date": today,
        },
    ).mappings().one()

    payment_data = db.execute(
        text("""
            SELECT
                COALESCE(SUM(amount), 0) AS received_amount,
                COUNT(*) AS payment_count
            FROM ar_payments
            WHERE company_code = :company_code
              AND payment_date >= :start_date
              AND payment_date <= :end_date
        """),
        {
            "company_code": company_code,
            "start_date": start_date,
            "end_date": today,
        },
    ).mappings().one()

    return {
        "company_code": company_code,
        "period": period,
        "start_date": start_date.isoformat(),
        "end_date": today.isoformat(),
        "posted_invoice_total": float(invoice_data["invoice_total"] or 0),
        "invoice_subtotal": float(invoice_data["subtotal_total"] or 0),
        "invoice_tax_total": float(invoice_data["tax_total"] or 0),
        "posted_invoice_count": int(invoice_data["invoice_count"]),
        "payments_received": float(payment_data["received_amount"] or 0),
        "payment_count": int(payment_data["payment_count"]),
        "note": "AR-only figures; not company-wide income, expenses, or net profit.",
    }
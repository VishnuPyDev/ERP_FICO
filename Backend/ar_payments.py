from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from ar_payment_schemas import PaymentCreate


router = APIRouter(
    prefix="/ar/payments",
    tags=["AR Payments"]
)


CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Convert a database amount to a two-decimal Decimal."""
    return Decimal(str(value)).quantize(
        CENT,
        rounding=ROUND_HALF_UP
    )


def get_invoice_payment_summary(
    db: Session,
    invoice_id: int
):
    """Calculate the total paid, balance and payment status."""

    invoice = db.execute(
        text("""
            SELECT
                id,
                company_code,
                customer_id,
                invoice_number,
                status,
                total_amount
            FROM ar_invoices
            WHERE id = :invoice_id
        """),
        {"invoice_id": invoice_id}
    ).mappings().first()

    if not invoice:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found."
        )

    payment_totals = db.execute(
        text("""
            SELECT COALESCE(SUM(amount), 0) AS total_paid
            FROM ar_payments
            WHERE invoice_id = :invoice_id
        """),
        {"invoice_id": invoice_id}
    ).mappings().first()

    invoice_amount = money(invoice["total_amount"])
    total_paid = money(payment_totals["total_paid"])
    outstanding = money(invoice_amount - total_paid)

    if total_paid == 0:
        payment_status = "UNPAID"
    elif outstanding == 0:
        payment_status = "PAID"
    else:
        payment_status = "PARTIALLY_PAID"

    return {
        "invoice_id": invoice["id"],
        "company_code": invoice["company_code"],
        "customer_id": invoice["customer_id"],
        "invoice_number": invoice["invoice_number"],
        "invoice_status": invoice["status"],
        "invoice_amount": invoice_amount,
        "total_paid": total_paid,
        "outstanding_amount": outstanding,
        "payment_status": payment_status
    }


@router.post("/", status_code=201)
def create_payment(
    payment: PaymentCreate,
    db: Session = Depends(get_db)
):
    """
    Record a customer payment against an invoice.
    Multiple partial payments are supported.
    """

    try:
        # 1. Lock the invoice row while checking its balance.
        # This helps prevent concurrent requests from overpaying it.
        invoice = db.execute(
            text("""
                SELECT
                    id,
                    company_code,
                    customer_id,
                    invoice_number,
                    status,
                    total_amount
                FROM ar_invoices
                WHERE id = :invoice_id
                FOR UPDATE
            """),
            {"invoice_id": payment.invoice_id}
        ).mappings().first()

        if not invoice:
            raise HTTPException(
                status_code=404,
                detail="Invoice not found."
            )

        # 2. Ensure the payment belongs to the invoice's company.
        if invoice["company_code"] != payment.company_code:
            raise HTTPException(
                status_code=400,
                detail="Invoice does not belong to this company."
            )

        # 3. A cancelled invoice cannot receive payments.
        if invoice["status"] == "CANCELLED":
            raise HTTPException(
                status_code=400,
                detail="Cannot record a payment against a cancelled invoice."
            )

        # 4. Check whether the payment reference already exists.
        duplicate = db.execute(
            text("""
                SELECT id
                FROM ar_payments
                WHERE company_code = :company_code
                  AND payment_reference = :payment_reference
                LIMIT 1
            """),
            {
                "company_code": payment.company_code,
                "payment_reference": payment.payment_reference
            }
        ).first()

        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="This payment reference already exists for this company."
            )

        # 5. Calculate the current outstanding balance.
        totals = db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0) AS total_paid
                FROM ar_payments
                WHERE invoice_id = :invoice_id
            """),
            {"invoice_id": payment.invoice_id}
        ).mappings().first()

        invoice_amount = money(invoice["total_amount"])
        total_paid = money(totals["total_paid"])
        outstanding = money(invoice_amount - total_paid)
        payment_amount = money(payment.amount)

        if outstanding <= 0:
            raise HTTPException(
                status_code=409,
                detail="This invoice is already fully paid."
            )

        # 6. Do not allow overpayment.
        if payment_amount > outstanding:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Payment exceeds the outstanding balance. "
                    f"Outstanding amount: {outstanding}"
                )
            )

        # 7. Save the payment.
        result = db.execute(
            text("""
                INSERT INTO ar_payments (
                    company_code,
                    invoice_id,
                    payment_reference,
                    payment_date,
                    amount,
                    payment_method,
                    notes
                )
                VALUES (
                    :company_code,
                    :invoice_id,
                    :payment_reference,
                    :payment_date,
                    :amount,
                    :payment_method,
                    :notes
                )
            """),
            {
                "company_code": payment.company_code,
                "invoice_id": payment.invoice_id,
                "payment_reference": payment.payment_reference,
                "payment_date": payment.payment_date,
                "amount": payment_amount,
                "payment_method": payment.payment_method,
                "notes": payment.notes
            }
        )

        payment_id = result.lastrowid

        # 8. Commit the payment.
        db.commit()

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Payment conflicts with an existing reference "
                "or database constraint."
            )
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while recording payment."
        ) from exc

    # 9. Return the saved payment and updated balance.
    saved_payment = db.execute(
        text("""
            SELECT *
            FROM ar_payments
            WHERE id = :payment_id
        """),
        {"payment_id": payment_id}
    ).mappings().first()

    summary = get_invoice_payment_summary(
        db,
        payment.invoice_id
    )

    return {
        "message": "Payment recorded successfully.",
        "payment": dict(saved_payment),
        "invoice_summary": summary
    }


@router.get("/")
def list_payments(
    company_code: str = Query(
        min_length=1,
        max_length=20
    ),
    invoice_id: int | None = Query(
        default=None,
        gt=0
    ),
    db: Session = Depends(get_db)
):
    """List payments for a company, optionally filtered by invoice."""

    sql = """
        SELECT
            p.id,
            p.company_code,
            p.invoice_id,
            i.invoice_number,
            p.payment_reference,
            p.payment_date,
            p.amount,
            p.payment_method,
            p.notes,
            p.created_at
        FROM ar_payments p
        JOIN ar_invoices i
          ON i.id = p.invoice_id
        WHERE p.company_code = :company_code
    """

    params = {"company_code": company_code}

    if invoice_id is not None:
        sql += " AND p.invoice_id = :invoice_id"
        params["invoice_id"] = invoice_id

    sql += " ORDER BY p.payment_date DESC, p.id DESC"

    rows = db.execute(
        text(sql),
        params
    ).mappings().all()

    return {
        "company_code": company_code,
        "total_payments": len(rows),
        "payments": [dict(row) for row in rows]
    }


@router.get("/outstanding")
def list_outstanding_invoices(
    company_code: str = Query(
        min_length=1,
        max_length=20
    ),
    db: Session = Depends(get_db)
):
    """List invoices that have an unpaid balance."""

    rows = db.execute(
        text("""
            SELECT
                i.id AS invoice_id,
                i.company_code,
                i.customer_id,
                i.invoice_number,
                i.invoice_date,
                i.due_date,
                i.status AS invoice_status,
                i.total_amount AS invoice_amount,
                COALESCE(SUM(p.amount), 0) AS total_paid
            FROM ar_invoices i
            LEFT JOIN ar_payments p
              ON p.invoice_id = i.id
            WHERE i.company_code = :company_code
              AND i.status <> 'CANCELLED'
            GROUP BY
                i.id,
                i.company_code,
                i.customer_id,
                i.invoice_number,
                i.invoice_date,
                i.due_date,
                i.status,
                i.total_amount
            HAVING
                i.total_amount - COALESCE(SUM(p.amount), 0) > 0
            ORDER BY i.due_date ASC, i.id ASC
        """),
        {"company_code": company_code}
    ).mappings().all()

    invoices = []

    for row in rows:
        invoice = dict(row)

        invoice_amount = money(invoice["invoice_amount"])
        total_paid = money(invoice["total_paid"])
        outstanding = money(invoice_amount - total_paid)

        invoice["invoice_amount"] = invoice_amount
        invoice["total_paid"] = total_paid
        invoice["outstanding_amount"] = outstanding
        invoice["payment_status"] = (
            "UNPAID" if total_paid == 0
            else "PARTIALLY_PAID"
        )

        invoices.append(invoice)

    return {
        "company_code": company_code,
        "total_outstanding_invoices": len(invoices),
        "invoices": invoices
    }


@router.get("/invoice/{invoice_id}/summary")
def get_invoice_payment_summary_api(
    invoice_id: int,
    company_code: str = Query(
        min_length=1,
        max_length=20
    ),
    db: Session = Depends(get_db)
):
    """Get payment totals and the outstanding balance for one invoice."""

    summary = get_invoice_payment_summary(
        db,
        invoice_id
    )

    if summary["company_code"] != company_code:
        raise HTTPException(
            status_code=404,
            detail="Invoice not found for this company."
        )

    return summary


@router.get("/{payment_id}")
def get_payment(
    payment_id: int,
    company_code: str = Query(
        min_length=1,
        max_length=20
    ),
    db: Session = Depends(get_db)
):
    """Get a single payment."""

    row = db.execute(
        text("""
            SELECT
                p.*,
                i.invoice_number,
                i.customer_id
            FROM ar_payments p
            JOIN ar_invoices i
              ON i.id = p.invoice_id
            WHERE p.id = :payment_id
              AND p.company_code = :company_code
        """),
        {
            "payment_id": payment_id,
            "company_code": company_code
        }
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Payment not found for this company."
        )

    return dict(row)
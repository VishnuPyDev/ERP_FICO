
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from ar_invoice_schemas import InvoiceCreate


router = APIRouter(
    prefix="/ar/invoices",
    tags=["Accounts Receivable - Invoices"]
)

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@router.post("/", status_code=201)
def create_invoice(
    invoice: InvoiceCreate,
    db: Session = Depends(get_db)
):
    company_code = invoice.company_code.strip()
    invoice_date = invoice.invoice_date

    if invoice.due_date is not None:
        due_date = invoice.due_date
    else:
        due_date = None

    if due_date is not None and due_date < invoice_date:
        raise HTTPException(
            status_code=422,
            detail="Due date cannot be before invoice date"
        )

    try:
        # Confirm that the customer belongs to this company
        # and is active.
        customer = db.execute(
            text("""
                SELECT id, payment_terms_days
                FROM ar_customers
                WHERE id = :customer_id
                  AND company_code = :company_code
                  AND is_active = TRUE
            """),
            {
                "customer_id": invoice.customer_id,
                "company_code": company_code
            }
        ).mappings().first()

        if customer is None:
            raise HTTPException(
                status_code=404,
                detail="Active customer not found for this company"
            )

        # Default due date is based on the customer's payment terms.
        if due_date is None:
            due_date = invoice_date + timedelta(
                days=customer["payment_terms_days"]
            )

        # Calculate amounts on the server.
        prepared_lines = []
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        for line in invoice.lines:
            line_subtotal = money(line.quantity * line.unit_price)
            line_tax = money(
                line_subtotal * line.tax_rate / Decimal("100")
            )
            line_total = line_subtotal + line_tax

            prepared_lines.append({
                "description": line.description,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "tax_rate": line.tax_rate,
                "line_subtotal": line_subtotal,
                "tax_amount": line_tax,
                "line_total": line_total
            })

            subtotal += line_subtotal
            tax_total += line_tax

        subtotal = money(subtotal)
        tax_total = money(tax_total)
        total_amount = subtotal + tax_total

        # Create the invoice header.
        result = db.execute(
            text("""
                INSERT INTO ar_invoices (
                    company_code,
                    customer_id,
                    invoice_number,
                    invoice_date,
                    due_date,
                    status,
                    subtotal,
                    tax_amount,
                    total_amount,
                    notes
                )
                VALUES (
                    :company_code,
                    :customer_id,
                    :invoice_number,
                    :invoice_date,
                    :due_date,
                    'DRAFT',
                    :subtotal,
                    :tax_amount,
                    :total_amount,
                    :notes
                )
            """),
            {
                "company_code": company_code,
                "customer_id": invoice.customer_id,
                "invoice_number": invoice.invoice_number,
                "invoice_date": invoice_date,
                "due_date": due_date,
                "subtotal": subtotal,
                "tax_amount": tax_total,
                "total_amount": total_amount,
                "notes": invoice.notes
            }
        )

        invoice_id = result.lastrowid

        # Save every line against the newly created invoice.
        for line in prepared_lines:
            db.execute(
                text("""
                    INSERT INTO ar_invoice_lines (
                        invoice_id,
                        description,
                        quantity,
                        unit_price,
                        tax_rate,
                        line_subtotal,
                        tax_amount,
                        line_total
                    )
                    VALUES (
                        :invoice_id,
                        :description,
                        :quantity,
                        :unit_price,
                        :tax_rate,
                        :line_subtotal,
                        :tax_amount,
                        :line_total
                    )
                """),
                {
                    "invoice_id": invoice_id,
                    **line
                }
            )

        # Commit the header and all lines together.
        db.commit()

        return {
            "message": "Draft invoice created successfully",
            "invoice_id": invoice_id,
            "company_code": company_code,
            "customer_id": invoice.customer_id,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "status": "DRAFT",
            "subtotal": subtotal,
            "tax_amount": tax_total,
            "total_amount": total_amount,
            "lines": [
                {"invoice_id": invoice_id, **line}
                for line in prepared_lines
            ]
        }

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Duplicate invoice number or database constraint violation"
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while creating invoice"
        ) from exc


@router.get("/")
def list_invoices(
    company_code: str = Query(min_length=1, max_length=20),
    status: str | None = Query(default=None),
    customer_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db)
):
    if status is not None:
        status = status.upper()
        if status not in {"DRAFT", "POSTED", "CANCELLED"}:
            raise HTTPException(
                status_code=422,
                detail="Status must be DRAFT, POSTED, or CANCELLED"
            )

    sql = """
        SELECT *
        FROM ar_invoices
        WHERE company_code = :company_code
    """
    params = {"company_code": company_code.strip()}

    if status:
        sql += " AND status = :status"
        params["status"] = status

    if customer_id is not None:
        sql += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    sql += " ORDER BY invoice_date DESC, id DESC"

    try:
        rows = db.execute(
            text(sql), params
        ).mappings().all()

        return {
            "company_code": company_code.strip(),
            "total_invoices": len(rows),
            "invoices": [dict(row) for row in rows]
        }

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Database error while listing invoices"
        ) from exc


@router.get("/{invoice_id}")
def get_invoice(
    invoice_id: int,
    company_code: str = Query(min_length=1, max_length=20),
    db: Session = Depends(get_db)
):
    try:
        header = db.execute(
            text("""
                SELECT *
                FROM ar_invoices
                WHERE id = :invoice_id
                  AND company_code = :company_code
            """),
            {
                "invoice_id": invoice_id,
                "company_code": company_code.strip()
            }
        ).mappings().first()

        if header is None:
            raise HTTPException(
                status_code=404,
                detail="Invoice not found for this company"
            )

        lines = db.execute(
            text("""
                SELECT
                    id,
                    invoice_id,
                    description,
                    quantity,
                    unit_price,
                    tax_rate,
                    line_subtotal,
                    tax_amount,
                    line_total
                FROM ar_invoice_lines
                WHERE invoice_id = :invoice_id
                ORDER BY id
            """),
            {"invoice_id": invoice_id}
        ).mappings().all()

        result = dict(header)
        result["lines"] = [dict(line) for line in lines]

        return result

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Database error while retrieving invoice"
        ) from exc


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from ar_schema import CustomerCreate, CustomerUpdate


router = APIRouter(
    prefix="/ar/customers",
    tags=["Accounts Receivable - Customers"]
)


def get_customer_or_404(customer_id: int, db: Session):
    result = db.execute(
        text("""
            SELECT *
            FROM ar_customers
            WHERE id = :customer_id
        """),
        {"customer_id": customer_id}
    ).mappings().first()

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found"
        )

    return dict(result)


@router.post("/", status_code=201)
def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db)
):
    data = customer.model_dump()

    try:
        result = db.execute(
            text("""
                INSERT INTO ar_customers (
                    company_code,
                    customer_code,
                    customer_name,
                    email,
                    phone,
                    address,
                    payment_terms_days,
                    credit_limit
                )
                VALUES (
                    :company_code,
                    :customer_code,
                    :customer_name,
                    :email,
                    :phone,
                    :address,
                    :payment_terms_days,
                    :credit_limit
                )
            """),
            data
        )

        customer_id = result.lastrowid
        db.commit()

        return get_customer_or_404(customer_id, db)

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Customer code already exists for this company, or the data violates a database constraint"
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while creating customer"
        ) from exc


@router.get("/")
def list_customers(
    company_code: str = Query(min_length=1, max_length=20),
    search: str | None = Query(default=None, max_length=150),
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    sql = """
        SELECT *
        FROM ar_customers
        WHERE company_code = :company_code
    """

    params = {"company_code": company_code.strip()}

    if search and search.strip():
        sql += """
            AND (
                customer_code LIKE :search
                OR customer_name LIKE :search
            )
        """
        params["search"] = f"%{search.strip()}%"

    if active_only:
        sql += " AND is_active = TRUE"

    sql += " ORDER BY customer_name"

    try:
        rows = db.execute(text(sql), params).mappings().all()
        return {
            "company_code": company_code.strip(),
            "total_customers": len(rows),
            "customers": [dict(row) for row in rows]
        }
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Database error while listing customers"
        ) from exc


@router.get("/{customer_id}")
def get_customer(
    customer_id: int,
    company_code: str = Query(min_length=1, max_length=20),
    db: Session = Depends(get_db)
):
    customer = db.execute(
        text("""
            SELECT *
            FROM ar_customers
            WHERE id = :customer_id
              AND company_code = :company_code
        """),
        {
            "customer_id": customer_id,
            "company_code": company_code.strip()
        }
    ).mappings().first()

    if customer is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found for this company"
        )

    return dict(customer)


@router.patch("/{customer_id}")
def update_customer(
    customer_id: int,
    customer: CustomerUpdate,
    company_code: str = Query(min_length=1, max_length=20),
    db: Session = Depends(get_db)
):
    existing = db.execute(
        text("""
            SELECT id
            FROM ar_customers
            WHERE id = :customer_id
              AND company_code = :company_code
        """),
        {
            "customer_id": customer_id,
            "company_code": company_code.strip()
        }
    ).first()

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found for this company"
        )

    data = customer.model_dump(exclude_unset=True)

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update"
        )

    allowed_fields = {
        "customer_name",
        "email",
        "phone",
        "address",
        "payment_terms_days",
        "credit_limit"
    }

    assignments = ", ".join(
        f"{field} = :{field}" for field in data
        if field in allowed_fields
    )

    data["customer_id"] = customer_id
    data["company_code"] = company_code.strip()

    try:
        db.execute(
            text(f"""
                UPDATE ar_customers
                SET {assignments}
                WHERE id = :customer_id
                  AND company_code = :company_code
            """),
            data
        )
        db.commit()

        return get_customer_or_404(customer_id, db)

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Updated data violates a database constraint"
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while updating customer"
        ) from exc


@router.patch("/{customer_id}/deactivate")
def deactivate_customer(
    customer_id: int,
    company_code: str = Query(min_length=1, max_length=20),
    db: Session = Depends(get_db)
):
    existing = db.execute(
        text("""
            SELECT id, is_active
            FROM ar_customers
            WHERE id = :customer_id
              AND company_code = :company_code
        """),
        {
            "customer_id": customer_id,
            "company_code": company_code.strip()
        }
    ).first()

    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Customer not found for this company"
        )

    if not existing.is_active:
        raise HTTPException(
            status_code=400,
            detail="Customer is already inactive"
        )

    try:
        db.execute(
            text("""
                UPDATE ar_customers
                SET is_active = FALSE
                WHERE id = :customer_id
                  AND company_code = :company_code
            """),
            {
                "customer_id": customer_id,
                "company_code": company_code.strip()
            }
        )
        db.commit()

        return {
            "message": "Customer deactivated successfully",
            "customer_id": customer_id
        }

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while deactivating customer"
        ) from exc

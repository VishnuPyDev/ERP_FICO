from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db


router = APIRouter(
    prefix="/companies",
    tags=["Company Setup"]
)


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class CompanyCreate(BaseModel):
    company_code: str = Field(min_length=1, max_length=20)
    company_name: str = Field(min_length=1, max_length=150)
    country: str = Field(min_length=1, max_length=100)
    currency: str = Field(min_length=3, max_length=10)
    financial_year_start: date
    company_address: Optional[str] = Field(
        default=None, max_length=255
    )


class CompanyUpdate(BaseModel):
    company_code: Optional[str] = Field(
        default=None, min_length=1, max_length=20
    )
    company_name: Optional[str] = Field(
        default=None, min_length=1, max_length=150
    )
    country: Optional[str] = Field(
        default=None, min_length=1, max_length=100
    )
    currency: Optional[str] = Field(
        default=None, min_length=3, max_length=10
    )
    financial_year_start: Optional[date] = None
    company_address: Optional[str] = Field(
        default=None, max_length=255
    )


class CompanyStatusUpdate(BaseModel):
    is_active: bool


# --------------------------------------------------
# HELPER: GET ONE COMPANY
# --------------------------------------------------

def get_company_or_404(db: Session, company_id: int):
    query = text("""
        SELECT
            id,
            company_code,
            company_name,
            country,
            currency,
            financial_year_start,
            company_address,
            is_active,
            created_at,
            updated_at
        FROM company
        WHERE id = :company_id
    """)

    row = db.execute(
        query,
        {"company_id": company_id}
    ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Company not found"
        )

    return dict(row)


# --------------------------------------------------
# 1. CREATE COMPANY
# POST /companies/
# --------------------------------------------------

@router.post("/", status_code=201)
def create_company(
    payload: CompanyCreate,
    db: Session = Depends(get_db)
):
    values = payload.model_dump()

    values["company_code"] = values["company_code"].strip().upper()
    values["company_name"] = values["company_name"].strip()
    values["country"] = values["country"].strip()
    values["currency"] = values["currency"].strip().upper()

    if not values["company_code"] or not values["company_name"]:
        raise HTTPException(
            status_code=422,
            detail="Company code and company name are required"
        )

    try:
        result = db.execute(
            text("""
                INSERT INTO company (
                    company_code,
                    company_name,
                    country,
                    currency,
                    financial_year_start,
                    company_address,
                    is_active
                )
                VALUES (
                    :company_code,
                    :company_name,
                    :country,
                    :currency,
                    :financial_year_start,
                    :company_address,
                    1
                )
            """),
            values
        )

        company_id = result.lastrowid
        db.commit()

        return get_company_or_404(db, company_id)

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This company code already exists"
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while creating company"
        ) from exc


# --------------------------------------------------
# 2. LIST AND SEARCH COMPANIES
# GET /companies/
# --------------------------------------------------

@router.get("/")
def list_companies(
    search: Optional[str] = Query(default=None, max_length=150),
    active_only: bool = False,
    db: Session = Depends(get_db)
):
    search_value = search.strip() if search and search.strip() else None

    try:
        rows = db.execute(
            text("""
                SELECT
                    id,
                    company_code,
                    company_name,
                    country,
                    currency,
                    financial_year_start,
                    company_address,
                    is_active,
                    created_at,
                    updated_at
                FROM company
                WHERE (
                    :search IS NULL
                    OR company_code LIKE :search_like
                    OR company_name LIKE :search_like
                )
                AND (
                    :active_only = 0
                    OR is_active = 1
                )
                ORDER BY company_name, company_code
            """),
            {
                "search": search_value,
                "search_like": (
                    f"%{search_value}%"
                    if search_value else "%"
                ),
                "active_only": 1 if active_only else 0
            }
        ).mappings().all()

        return {
            "total": len(rows),
            "companies": [dict(row) for row in rows]
        }

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail="Database error while retrieving companies"
        ) from exc


# --------------------------------------------------
# 3. GET COMPANY BY ID
# GET /companies/{company_id}
# --------------------------------------------------

@router.get("/{company_id}")
def get_company(
    company_id: int,
    db: Session = Depends(get_db)
):
    return get_company_or_404(db, company_id)


# --------------------------------------------------
# 4. UPDATE COMPANY
# PUT /companies/{company_id}
# --------------------------------------------------

@router.put("/{company_id}")
def update_company(
    company_id: int,
    payload: CompanyUpdate,
    db: Session = Depends(get_db)
):
    get_company_or_404(db, company_id)

    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update"
        )

    for field in (
        "company_code",
        "company_name",
        "country",
        "currency"
    ):
        if field in changes and changes[field] is not None:
            changes[field] = changes[field].strip()

    if changes.get("company_code"):
        changes["company_code"] = changes["company_code"].upper()

    if changes.get("currency"):
        changes["currency"] = changes["currency"].upper()

    try:
        assignments = ", ".join(
            f"{field} = :{field}" for field in changes
        )

        changes["company_id"] = company_id

        db.execute(
            text(f"""
                UPDATE company
                SET {assignments}
                WHERE id = :company_id
            """),
            changes
        )

        db.commit()

        return get_company_or_404(db, company_id)

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This company code already exists"
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while updating company"
        ) from exc


# --------------------------------------------------
# 5. ACTIVATE OR DEACTIVATE COMPANY
# PATCH /companies/{company_id}/status
# --------------------------------------------------

@router.patch("/{company_id}/status")
def update_company_status(
    company_id: int,
    payload: CompanyStatusUpdate,
    db: Session = Depends(get_db)
):
    get_company_or_404(db, company_id)

    try:
        db.execute(
            text("""
                UPDATE company
                SET is_active = :is_active
                WHERE id = :company_id
            """),
            {
                "is_active": 1 if payload.is_active else 0,
                "company_id": company_id
            }
        )

        db.commit()

        return get_company_or_404(db, company_id)

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database error while updating company status"
        ) from exc
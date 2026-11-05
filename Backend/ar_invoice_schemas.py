
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator


class InvoiceLineCreate(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0, max_digits=15, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=15, decimal_places=2)
    tax_rate: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        le=100,
        max_digits=5,
        decimal_places=2
    )

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Description cannot be blank")
        return value


class InvoiceCreate(BaseModel):
    company_code: str = Field(min_length=1, max_length=20)
    customer_id: int = Field(gt=0)
    invoice_number: str = Field(min_length=1, max_length=40)
    invoice_date: date
    due_date: Optional[date] = None
    notes: Optional[str] = None
    lines: list[InvoiceLineCreate] = Field(min_length=1)

    @field_validator("company_code", "invoice_number")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_code: str
    customer_id: int
    invoice_number: str
    invoice_date: date
    due_date: date
    status: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal

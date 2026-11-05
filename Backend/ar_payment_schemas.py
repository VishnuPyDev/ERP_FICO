from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


PaymentMethod = Literal[
    "BANK_TRANSFER",
    "CASH",
    "UPI",
    "CHEQUE",
    "CARD",
    "OTHER"
]


class PaymentCreate(BaseModel):
    company_code: str = Field(
        min_length=1,
        max_length=20
    )

    invoice_id: int = Field(gt=0)

    payment_reference: str = Field(
        min_length=1,
        max_length=50
    )

    payment_date: date

    amount: Decimal = Field(
        gt=0,
        max_digits=15,
        decimal_places=2
    )

    payment_method: PaymentMethod

    notes: str | None = Field(
        default=None,
        max_length=500
    )


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_code: str
    invoice_id: int
    payment_reference: str
    payment_date: date
    amount: Decimal
    payment_method: str
    notes: str | None = None
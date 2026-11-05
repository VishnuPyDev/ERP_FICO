
from datetime import datetime
from decimal import Decimal
from typing import Optional


from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator


class CustomerCreate(BaseModel):
    company_code: str = Field(min_length=1, max_length=20)
    customer_code: str = Field(min_length=1, max_length=30)
    customer_name: str = Field(min_length=1, max_length=150)

    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = None

    payment_terms_days: int = Field(default=30, ge=0)
    credit_limit: Decimal = Field(default=Decimal("0.00"), ge=0)

    @field_validator("company_code", "customer_code", "customer_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, value):
        return value.strip() if value else value


class CustomerUpdate(BaseModel):
    customer_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = None
    payment_terms_days: Optional[int] = Field(default=None, ge=0)
    credit_limit: Optional[Decimal] = Field(default=None, ge=0)

    @field_validator("customer_name")
    @classmethod
    def strip_customer_name(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("Customer name cannot be blank")
        return value

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, value):
        return value.strip() if value else value


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_code: str
    customer_code: str
    customer_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms_days: int
    credit_limit: Decimal
    is_active: bool
    created_at: datetime

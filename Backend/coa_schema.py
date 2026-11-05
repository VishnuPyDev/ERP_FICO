
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


AccountType = Literal[
    "ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"
]

NormalBalance = Literal["DEBIT", "CREDIT"]


class AccountCreate(BaseModel):
    company_code: str = Field(min_length=1, max_length=20)
    account_code: str = Field(min_length=1, max_length=20)
    account_name: str = Field(min_length=1, max_length=100)
    account_type: AccountType
    parent_account_id: int | None = None
    normal_balance: NormalBalance
    description: str | None = Field(default=None, max_length=255)

    @field_validator("company_code", "account_code", "account_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank")
        return value


class AccountUpdate(BaseModel):
    account_name: str | None = Field(default=None, max_length=100)
    account_type: AccountType | None = None
    parent_account_id: int | None = None
    normal_balance: NormalBalance | None = None
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_code: str
    account_code: str
    account_name: str
    account_type: AccountType
    parent_account_id: int | None
    normal_balance: NormalBalance
    description: str | None
    is_active: bool

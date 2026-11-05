
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db


router = APIRouter(
    prefix="/financial-reports",
    tags=["Financial Reports"]
)


# --------------------------------------------------
# TRIAL BALANCE
# --------------------------------------------------

@router.get("/trial-balance")
def get_trial_balance(
    company_code: str = Query(..., description="Company code"),
    start_date: date = Query(..., description="Report start date"),
    end_date: date = Query(..., description="Report end date"),
    db: Session = Depends(get_db)
):
    """
    Trial Balance:
    Shows account-wise debit and credit totals
    for posted journal entries in the selected period.
    """

    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="Start date cannot be after end date."
        )

    query = text("""
        SELECT
            coa.company_code,
            coa.account_code,
            coa.account_name,
            coa.account_type,
            COALESCE(SUM(jl.debit), 0) AS total_debit,
            COALESCE(SUM(jl.credit), 0) AS total_credit,
            COALESCE(SUM(jl.debit), 0)
                - COALESCE(SUM(jl.credit), 0) AS net_balance
        FROM chart_of_accounts coa

        LEFT JOIN journal_lines jl
            ON jl.account_code = coa.account_code

        LEFT JOIN journal_entries je
            ON je.id = jl.journal_entry_id
            AND je.status = 'POSTED'
            AND je.entry_date >= :start_date
            AND je.entry_date < DATE_ADD(
                :end_date,
                INTERVAL 1 DAY
            )

        WHERE coa.company_code = :company_code
          AND coa.is_active = 1

        GROUP BY
            coa.company_code,
            coa.account_code,
            coa.account_name,
            coa.account_type

        ORDER BY coa.account_code
    """)

    try:
        # Use the report dates and company code in the query.
        # Keep journal entry filters in the JOIN so accounts
        # without transactions can still appear with zero totals.
        result = db.execute(
            query,
            {
                "company_code": company_code,
                "start_date": start_date,
                "end_date": end_date
            }
        ).mappings().all()

        accounts = []
        total_debit = 0.0
        total_credit = 0.0

        for row in result:
            debit = float(row["total_debit"] or 0)
            credit = float(row["total_credit"] or 0)

            accounts.append({
                "company_code": row["company_code"],
                "account_code": row["account_code"],
                "account_name": row["account_name"],
                "account_type": row["account_type"],
                "debit": round(debit, 2),
                "credit": round(credit, 2),
                "net_balance": round(debit - credit, 2)
            })

            total_debit += debit
            total_credit += credit

        difference = total_debit - total_credit

        return {
            "report": "Trial Balance",
            "company_code": company_code,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "accounts": accounts,
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "difference": round(difference, 2),
            "is_balanced": abs(difference) < 0.01
        }

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate Trial Balance: {str(exc)}"
        ) from exc


# --------------------------------------------------
# PROFIT AND LOSS
# --------------------------------------------------

@router.get("/profit-loss")
def get_profit_loss(
    company_code: str = Query(..., description="Company code"),
    start_date: date = Query(..., description="Report start date"),
    end_date: date = Query(..., description="Report end date"),
    db: Session = Depends(get_db)
):
    """
    Profit & Loss:
    Calculates revenue, expenses, and net profit/loss
    using posted journal entries in the selected period.
    """

    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="Start date cannot be after end date."
        )

    query = text("""
        SELECT
            coa.account_code,
            coa.account_name,
            UPPER(coa.account_type) AS account_type,
            COALESCE(SUM(jl.debit), 0) AS total_debit,
            COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM chart_of_accounts coa

        LEFT JOIN journal_lines jl
            ON jl.account_code = coa.account_code

        LEFT JOIN journal_entries je
            ON je.id = jl.journal_entry_id
            AND je.status = 'POSTED'
            AND je.entry_date >= :start_date
            AND je.entry_date < DATE_ADD(
                :end_date,
                INTERVAL 1 DAY
            )

        WHERE coa.company_code = :company_code
          AND coa.is_active = 1
          AND UPPER(coa.account_type) IN (
              'REVENUE',
              'EXPENSE'
          )

        GROUP BY
            coa.account_code,
            coa.account_name,
            UPPER(coa.account_type)

        ORDER BY
            UPPER(coa.account_type),
            coa.account_code
    """)

    try:
        result = db.execute(
            query,
            {
                "company_code": company_code,
                "start_date": start_date,
                "end_date": end_date
            }
        ).mappings().all()

        revenue_accounts = []
        expense_accounts = []

        total_revenue = 0.0
        total_expenses = 0.0

        for row in result:
            debit = float(row["total_debit"] or 0)
            credit = float(row["total_credit"] or 0)

            account_type = row["account_type"]

            if account_type == "REVENUE":
                # Revenue normally increases with credits.
                amount = credit - debit

                revenue_accounts.append({
                    "account_code": row["account_code"],
                    "account_name": row["account_name"],
                    "amount": round(amount, 2)
                })

                total_revenue += amount

            elif account_type == "EXPENSE":
                # Expenses normally increase with debits.
                amount = debit - credit

                expense_accounts.append({
                    "account_code": row["account_code"],
                    "account_name": row["account_name"],
                    "amount": round(amount, 2)
                })

                total_expenses += amount

        net_profit_loss = total_revenue - total_expenses

        return {
            "report": "Profit and Loss",
            "company_code": company_code,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "revenue": {
                "accounts": revenue_accounts,
                "total": round(total_revenue, 2)
            },
            "expenses": {
                "accounts": expense_accounts,
                "total": round(total_expenses, 2)
            },
            "net_profit_loss": round(net_profit_loss, 2),
            "result": (
                "PROFIT"
                if net_profit_loss > 0
                else "LOSS"
                if net_profit_loss < 0
                else "BREAK_EVEN"
            )
        }

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate Profit & Loss: {str(exc)}"
        ) from exc

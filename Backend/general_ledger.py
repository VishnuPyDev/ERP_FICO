"""
STACKLY OneERP FICO - General Ledger API
Read-only report based on posted journal entries. No duplicate GL table is created.

Existing schema expected from this project's Journal Entry module:
company(id, company_code)
chart_of_accounts(id, company_code, account_code, account_name, account_type,
                  normal_balance, is_active)
fico_fiscal_years(id, company_id, year_code, year_name, start_date, end_date, status)
fico_fiscal_periods(id, company_id, fiscal_year_id, period_name, start_date, end_date, status)
fico_journal_entries(id, company_id, company_code, fiscal_year_id, fiscal_period_id,
                     entry_number, entry_date, posting_date, description,
                     reference_number, currency, status, entry_type)
fico_journal_entry_lines(id, journal_entry_id, line_number, account_id,
                         account_code, account_name, line_description,
                         debit, credit, cost_center)

Register in main.py:
from general_ledger import router as general_ledger_router
app.include_router(general_ledger_router)
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from database import get_db
except ImportError:
    from .database import get_db

router = APIRouter(prefix="/api/general-ledger", tags=["General Ledger"])
CENT = Decimal("0.01")


def dec(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def company_row(db, company_id):
    row = db.execute(text("""
        SELECT id, company_code FROM company
        WHERE id=:company_id LIMIT 1
    """), {"company_id": company_id}).mappings().first()
    if not row:
        raise HTTPException(404, "Company not found. Check Company ID.")
    return dict(row)


def get_filters(db, company_id, fiscal_year_id, fiscal_period_id,
                account_id, date_from, date_to):
    company = company_row(db, company_id)
    year = period = account = None

    if fiscal_year_id:
        year = db.execute(text("""
            SELECT id, company_id, year_code, year_name, start_date, end_date, status
            FROM fico_fiscal_years WHERE id=:id AND company_id=:company_id
        """), {"id": fiscal_year_id, "company_id": company_id}).mappings().first()
        if not year:
            raise HTTPException(404, "Fiscal year not found for this company.")
        year = dict(year)

    if fiscal_period_id:
        period = db.execute(text("""
            SELECT id, company_id, fiscal_year_id, period_name, start_date, end_date, status
            FROM fico_fiscal_periods WHERE id=:id AND company_id=:company_id
        """), {"id": fiscal_period_id, "company_id": company_id}).mappings().first()
        if not period:
            raise HTTPException(404, "Fiscal period not found for this company.")
        period = dict(period)
        if year and period["fiscal_year_id"] != year["id"]:
            raise HTTPException(422, "Fiscal period does not belong to the selected fiscal year.")
        if not year:
            year = db.execute(text("""
                SELECT id, company_id, year_code, year_name, start_date, end_date, status
                FROM fico_fiscal_years WHERE id=:id AND company_id=:company_id
            """), {"id": period["fiscal_year_id"], "company_id": company_id}).mappings().first()
            year = dict(year) if year else None

    if date_from and date_to and date_to < date_from:
        raise HTTPException(422, "Date To must be on or after Date From.")

    if date_from and year and not (year["start_date"] <= date_from <= year["end_date"]):
        raise HTTPException(422, "Date From must be within the selected fiscal year.")
    if date_to and year and not (year["start_date"] <= date_to <= year["end_date"]):
        raise HTTPException(422, "Date To must be within the selected fiscal year.")

    if date_from and period and not (period["start_date"] <= date_from <= period["end_date"]):
        raise HTTPException(422, "Date From must be within the selected fiscal period.")
    if date_to and period and not (period["start_date"] <= date_to <= period["end_date"]):
        raise HTTPException(422, "Date To must be within the selected fiscal period.")

    if account_id:
        account = db.execute(text("""
            SELECT id, account_code, account_name, account_type,
                   normal_balance, is_active
            FROM chart_of_accounts
            WHERE id=:account_id AND company_code=:company_code
        """), {"account_id": account_id, "company_code": company["company_code"]}).mappings().first()
        if not account:
            raise HTTPException(404, "Account not found for this company.")
        # Historical ledger transactions remain reportable after an account is
        # deactivated; inactivity blocks new postings, not historical reporting.
        account = dict(account)

    lower = date_from or (period["start_date"] if period else
                          year["start_date"] if year else None)
    upper = date_to or (period["end_date"] if period else
                        year["end_date"] if year else None)
    return company, year, period, account, lower, upper


def opening_balances(db, company, account_ids, cutoff):
    if not cutoff:
        return {}
    params = {"company_id": company["id"], "company_code": company["company_code"],
              "cutoff": cutoff}
    account_sql = ""
    if account_ids:
        keys = []
        for i, aid in enumerate(account_ids):
            key = f"a{i}"
            keys.append(":" + key)
            params[key] = aid
        account_sql = " AND l.account_id IN (" + ",".join(keys) + ")"
    rows = db.execute(text(f"""
        SELECT l.account_id, COALESCE(SUM(l.debit),0) debit,
               COALESCE(SUM(l.credit),0) credit
        FROM fico_journal_entry_lines l
        JOIN fico_journal_entries h ON h.id=l.journal_entry_id
        WHERE h.company_id=:company_id AND h.company_code=:company_code
          AND h.status IN ('POSTED','REVERSED')
          AND h.posting_date < :cutoff
          {account_sql}
        GROUP BY l.account_id
    """), params).mappings().all()
    return {int(r["account_id"]): (dec(r["debit"]), dec(r["credit"])) for r in rows}


def make_report(db, company, year, period, account, lower, upper,
                search, limit, offset):
    params = {"company_id": company["id"], "company_code": company["company_code"]}
    where = [
        "h.company_id=:company_id",
        "h.company_code=:company_code",
        "h.status IN ('POSTED','REVERSED')"
    ]
    if account:
        where.append("l.account_id=:account_id")
        params["account_id"] = account["id"]
    if year:
        where.append("h.fiscal_year_id=:fiscal_year_id")
        params["fiscal_year_id"] = year["id"]
    if period:
        where.append("h.fiscal_period_id=:fiscal_period_id")
        params["fiscal_period_id"] = period["id"]
    if lower:
        where.append("h.posting_date>=:lower_date")
        params["lower_date"] = lower
    if upper:
        where.append("h.posting_date<=:upper_date")
        params["upper_date"] = upper
    rows = db.execute(text(f"""
        SELECT h.id journal_entry_id, h.entry_number, h.entry_date, h.posting_date,
               h.description journal_description, h.reference_number, h.currency,
               h.status journal_status, h.entry_type, h.fiscal_year_id,
               h.fiscal_period_id, l.id journal_line_id, l.line_number,
               l.account_id, l.account_code, l.account_name, l.line_description,
               l.debit, l.credit, l.cost_center
        FROM fico_journal_entry_lines l
        JOIN fico_journal_entries h ON h.id=l.journal_entry_id
        WHERE {" AND ".join(where)}
        ORDER BY l.account_code, h.posting_date, h.id, l.line_number
    """), params).mappings().all()

    account_ids = sorted({int(r["account_id"]) for r in rows})
    if account:
        if account["id"] not in account_ids:
            account_ids.append(account["id"])
    else:
        company_account_ids = db.execute(text("""
            SELECT id FROM chart_of_accounts WHERE company_code=:company_code
        """), {"company_code": company["company_code"]}).scalars().all()
        account_ids = sorted(set(account_ids).union(int(x) for x in company_account_ids))

    opening = opening_balances(db, company, account_ids, lower)
    running = {}
    opening_data = {}
    for aid in account_ids:
        od, oc = opening.get(aid, (Decimal("0.00"), Decimal("0.00")))
        net = od - oc
        running[aid] = net
        opening_data[str(aid)] = {
            "debit": float(od), "credit": float(oc),
            "balance": float(abs(net)),
            "side": "DR" if net > 0 else "CR" if net < 0 else "ZERO"
        }

    result = []
    total_debit = total_credit = Decimal("0.00")
    for r in rows:
        aid = int(r["account_id"])
        debit, credit = dec(r["debit"]), dec(r["credit"])
        running[aid] = running.get(aid, Decimal("0.00")) + debit - credit
        balance = running[aid]
        result.append({
            "journal_entry_id": int(r["journal_entry_id"]),
            "journal_line_id": int(r["journal_line_id"]),
            "entry_number": r["entry_number"],
            "entry_date": r["entry_date"].isoformat() if r["entry_date"] else None,
            "posting_date": r["posting_date"].isoformat() if r["posting_date"] else None,
            "journal_description": r["journal_description"],
            "line_description": r["line_description"],
            "reference_number": r["reference_number"],
            "currency": r["currency"],
            "journal_status": r["journal_status"],
            "entry_type": r["entry_type"],
            "fiscal_year_id": int(r["fiscal_year_id"]),
            "fiscal_period_id": int(r["fiscal_period_id"]),
            "line_number": int(r["line_number"]),
            "account_id": aid,
            "account_code": r["account_code"],
            "account_name": r["account_name"],
            "cost_center": r["cost_center"],
            "debit": float(debit),
            "credit": float(credit),
            "running_balance": float(abs(balance)),
            "running_balance_side": "DR" if balance > 0 else "CR" if balance < 0 else "ZERO"
        })
        total_debit += debit
        total_credit += credit

    # Apply text search after calculating running balances so searching for
    # one journal does not incorrectly reset the balance to the searched lines.
    visible = result
    if search and search.strip():
        term = search.strip().lower()
        visible = [
            item for item in result
            if term in str(item.get("entry_number") or "").lower()
            or term in str(item.get("journal_description") or "").lower()
            or term in str(item.get("reference_number") or "").lower()
            or term in str(item.get("account_code") or "").lower()
            or term in str(item.get("account_name") or "").lower()
        ]

    page = visible[offset:offset + limit]
    return {
        "company_code": company["company_code"],
        "filters": {
            "fiscal_year_id": year["id"] if year else None,
            "fiscal_period_id": period["id"] if period else None,
            "date_from": lower.isoformat() if lower else None,
            "date_to": upper.isoformat() if upper else None,
            "account_id": account["id"] if account else None
        },
        "opening_balances": opening_data,
        "rows": page,
        "totals": {
            "transaction_count": len(visible),
            "total_debit": float(sum((dec(x["debit"]) for x in visible), Decimal("0.00"))),
            "total_credit": float(sum((dec(x["credit"]) for x in visible), Decimal("0.00"))),
            "difference": float(sum((dec(x["debit"]) - dec(x["credit"]) for x in visible), Decimal("0.00")))
        },
        "pagination": {
            "limit": limit, "offset": offset, "returned": len(page),
            "total": len(visible),
            "has_more": offset + limit < len(visible)
        }
    }


@router.get("/health")
def health():
    return {"module": "General Ledger", "status": "ready",
            "source": "posted journal entries"}


@router.get("/accounts")
def list_accounts(company_id: int = Query(gt=0), search: Optional[str] = None,
                  include_inactive: bool = True, db: Session = Depends(get_db)):
    company = company_row(db, company_id)
    sql = """
        SELECT id, account_code, account_name, account_type,
               normal_balance, is_active
        FROM chart_of_accounts WHERE company_code=:company_code
    """
    params = {"company_code": company["company_code"]}
    if not include_inactive:
        sql += " AND is_active=TRUE"
    if search and search.strip():
        sql += " AND (account_code LIKE :term OR account_name LIKE :term)"
        params["term"] = f"%{search.strip()}%"
    sql += " ORDER BY account_code"
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


@router.get("/summary")
def summary(company_id: int = Query(gt=0),
            fiscal_year_id: Optional[int] = Query(None, gt=0),
            fiscal_period_id: Optional[int] = Query(None, gt=0),
            date_from: Optional[date] = None, date_to: Optional[date] = None,
            db: Session = Depends(get_db)):
    company, year, period, _, lower, upper = get_filters(
        db, company_id, fiscal_year_id, fiscal_period_id, None, date_from, date_to)
    params = {"company_id": company_id, "company_code": company["company_code"]}
    where = ["h.company_id=:company_id", "h.company_code=:company_code",
             "h.status IN ('POSTED','REVERSED')"]
    if year:
        where.append("h.fiscal_year_id=:fy")
        params["fy"] = year["id"]
    if period:
        where.append("h.fiscal_period_id=:fp")
        params["fp"] = period["id"]
    if lower:
        where.append("h.posting_date>=:lo")
        params["lo"] = lower
    if upper:
        where.append("h.posting_date<=:hi")
        params["hi"] = upper
    row = db.execute(text(f"""
        SELECT COUNT(DISTINCT h.id) transaction_count,
               COUNT(DISTINCT l.account_id) account_count,
               COALESCE(SUM(l.debit),0) total_debit,
               COALESCE(SUM(l.credit),0) total_credit
        FROM fico_journal_entries h
        JOIN fico_journal_entry_lines l ON l.journal_entry_id=h.id
        WHERE {" AND ".join(where)}
    """), params).mappings().one()
    debit, credit = dec(row["total_debit"]), dec(row["total_credit"])
    return {
        "account_count": int(row["account_count"] or 0),
        "transaction_count": int(row["transaction_count"] or 0),
        "total_debit": float(debit), "total_credit": float(credit),
        "difference": float(debit - credit)
    }


@router.get("/ledger")
def ledger(company_id: int = Query(gt=0),
           account_id: Optional[int] = Query(None, gt=0),
           fiscal_year_id: Optional[int] = Query(None, gt=0),
           fiscal_period_id: Optional[int] = Query(None, gt=0),
           date_from: Optional[date] = None, date_to: Optional[date] = None,
           search: Optional[str] = None,
           limit: int = Query(200, ge=1, le=2000),
           offset: int = Query(0, ge=0),
           db: Session = Depends(get_db)):
    company, year, period, account, lower, upper = get_filters(
        db, company_id, fiscal_year_id, fiscal_period_id,
        account_id, date_from, date_to)
    return make_report(db, company, year, period, account,
                       lower, upper, search, limit, offset)


@router.get("/accounts/{account_id}/ledger")
def account_ledger(account_id: int, company_id: int = Query(gt=0),
                   fiscal_year_id: Optional[int] = Query(None, gt=0),
                   fiscal_period_id: Optional[int] = Query(None, gt=0),
                   date_from: Optional[date] = None, date_to: Optional[date] = None,
                   search: Optional[str] = None,
                   limit: int = Query(500, ge=1, le=2000),
                   offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db)):
    company, year, period, account, lower, upper = get_filters(
        db, company_id, fiscal_year_id, fiscal_period_id,
        account_id, date_from, date_to)
    return make_report(db, company, year, period, account,
                       lower, upper, search, limit, offset)

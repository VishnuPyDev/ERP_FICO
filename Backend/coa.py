
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from coa_schema import AccountCreate, AccountUpdate

router = APIRouter(
    prefix="/chart-of-accounts",
    tags=["Chart of Accounts"]
)


def account_query():
    return text("""
        SELECT id, company_code, account_code, account_name,
               account_type, parent_account_id, normal_balance,
               description, is_active
        FROM chart_of_accounts
    """)



@router.post("/", status_code=201)
def create_account(
    account: AccountCreate,
    db: Session = Depends(get_db)
):
    # 1. Check whether this account code already exists
    duplicate = db.execute(
        text("""
            SELECT id
            FROM chart_of_accounts
            WHERE company_code = :company_code
              AND account_code = :account_code
            LIMIT 1
        """),
        {
            "company_code": account.company_code,
            "account_code": account.account_code
        }
    ).fetchone()

    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="This account code already exists for this company."
        )
    
    # 2. Validate the parent account, if provided
    if account.parent_account_id is not None:
        parent = db.execute(
            text("""
                SELECT id, is_active
                FROM chart_of_accounts
                WHERE id = :parent_id
                  AND company_code = :company_code
                LIMIT 1
            """),
            {
                "parent_id": account.parent_account_id,
                "company_code": account.company_code
            }
        ).mappings().first()

        if not parent:
            raise HTTPException(
                status_code=400,
                detail="Parent account does not exist for this company."
            )

        if not parent["is_active"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot use an inactive account as the parent."
            )

    # 3. Insert the account into MySQL
    try:
        result = db.execute(
            text("""
                INSERT INTO chart_of_accounts (
                    company_code,
                    account_code,
                    account_name,
                    account_type,
                    parent_account_id,
                    normal_balance,
                    description,
                    is_active
                )
                VALUES (
                    :company_code,
                    :account_code,
                    :account_name,
                    :account_type,
                    :parent_account_id,
                    :normal_balance,
                    :description,
                    TRUE
                )
            """),
            {
                "company_code": account.company_code,
                "account_code": account.account_code,
                "account_name": account.account_name,
                "account_type": account.account_type,
                "parent_account_id": account.parent_account_id,
                "normal_balance": account.normal_balance,
                "description": account.description
            }
        )

        new_id = result.lastrowid
        db.commit()

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Account conflicts with an existing record or database constraint."
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database operation failed."
        ) from exc

    # 4. Retrieve and return the newly created account
    saved_account = db.execute(
        text("""
            SELECT *
            FROM chart_of_accounts
            WHERE id = :account_id
        """),
        {"account_id": new_id}
    ).mappings().first()

    return {
        "message": "Account created successfully",
        "account": dict(saved_account)
    }

    
@router.get("/")
def list_accounts(
    company_code: str = Query(min_length=1, max_length=20),
    search: str | None = None,
    db: Session = Depends(get_db)
):
    sql = """
        SELECT id, company_code, account_code, account_name,
               account_type, parent_account_id, normal_balance,
               description, is_active
        FROM chart_of_accounts
        WHERE company_code = :company_code
    """
    params = {"company_code": company_code}

    if search and search.strip():
        sql += """
            AND (account_code LIKE :search
                 OR account_name LIKE :search)
        """
        params["search"] = f"%{search.strip()}%"

    sql += " ORDER BY account_code"

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]

@router.get("/hierarchy/{company_code}")
def get_account_hierarchy(
    company_code: str,
    db: Session = Depends(get_db)
):
    # Step 1: Get all accounts for this company
    rows = db.execute(
        text("""
            SELECT
                id,
                company_code,
                account_code,
                account_name,
                account_type,
                parent_account_id,
                normal_balance,
                description,
                is_active
            FROM chart_of_accounts
            WHERE company_code = :company_code
            ORDER BY account_code
        """),
        {"company_code": company_code}
    ).mappings().all()

    # Step 2: Check whether the company has accounts
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No accounts found for this company"
        )

    # Step 3: Create a node for each account
    accounts = {}

    for row in rows:
        account = dict(row)
        account["children"] = []
        accounts[account["id"]] = account

    # Step 4: Connect child accounts to their parents
    roots = []

    for account in accounts.values():
        parent_id = account["parent_account_id"]

        if parent_id is None:
            roots.append(account)

        elif parent_id in accounts:
            accounts[parent_id]["children"].append(account)

        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Parent account {parent_id} is missing "
                    f"from company {company_code}"
                )
            )

    # Step 5: Check that every account is reachable
    # from a root account
    visited = set()
    stack = list(roots)

    while stack:
        account = stack.pop()
        account_id = account["id"]

        if account_id in visited:
            raise HTTPException(
                status_code=400,
                detail="Circular account hierarchy detected"
            )

        visited.add(account_id)
        stack.extend(account["children"])

    if len(visited) != len(accounts):
        raise HTTPException(
            status_code=400,
            detail=(
                "Some accounts are not connected to a root account. "
                "Check for circular parent-child relationships."
            )
        )

    # Step 6: Return the hierarchy
    return {
        "company_code": company_code,
        "total_accounts": len(accounts),
        "accounts": roots
    }

@router.get("/{account_id}")
def get_account(
    account_id: int,
    db: Session = Depends(get_db)
):
    row = db.execute(
        text("""
            SELECT id, company_code, account_code, account_name,
                   account_type, parent_account_id, normal_balance,
                   description, is_active
            FROM chart_of_accounts
            WHERE id = :id
        """),
        {"id": account_id}
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Account not found"
        )

    return dict(row)

@router.patch("/{account_id}")
def update_account(
    account_id: int,
    account: AccountUpdate,
    db: Session = Depends(get_db)
):
    current = db.execute(
        text("""
            SELECT id, company_code, is_active
            FROM chart_of_accounts
            WHERE id = :id
        """),
        {"id": account_id}
    ).mappings().first()

    if not current:
        raise HTTPException(
            status_code=404,
            detail="Account not found"
        )

    if not current["is_active"]:
        raise HTTPException(
            status_code=409,
            detail="Cannot update an inactive account. Activate the account first."
        )

    changes = account.model_dump(exclude_unset=True)

    # Only allow these fields to be updated
    allowed_fields = {
        "account_name",
        "account_type",
        "normal_balance",
        "parent_account_id",
        "description"
    }

    invalid_fields = set(changes) - allowed_fields

    if invalid_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Fields cannot be updated here: {sorted(invalid_fields)}"
        )

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update"
        )

    # Validate account name
    if "account_name" in changes:
        name = changes["account_name"]
        if name is None or not name.strip():
            raise HTTPException(
                status_code=422,
                detail="Account name cannot be empty"
            )
        changes["account_name"] = name.strip()

    # Validate account type
    valid_types = {
        "ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"
    }

    if (
        "account_type" in changes
        and changes["account_type"] not in valid_types
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid account type"
        )

    # Validate normal balance
    if (
        "normal_balance" in changes
        and changes["normal_balance"] not in {"DEBIT", "CREDIT"}
    ):
        raise HTTPException(
            status_code=422,
            detail="Normal balance must be DEBIT or CREDIT"
        )

    # Validate parent account
    if "parent_account_id" in changes:
        parent_id = changes["parent_account_id"]

        if parent_id == account_id:
            raise HTTPException(
                status_code=400,
                detail="An account cannot be its own parent"
            )

        if parent_id is not None:
            parent = db.execute(
                text("""
                    SELECT id
                    FROM chart_of_accounts
                    WHERE id = :parent_id
                      AND company_code = :company_code
                """),
                {
                    "parent_id": parent_id,
                    "company_code": current["company_code"]
                }
            ).first()

            if not parent:
                raise HTTPException(
                    status_code=400,
                    detail="Parent account does not exist in this company"
                )

            # Prevent circular parent-child relationships
            pending_parent_id = parent_id
            visited_ids = set()

            while pending_parent_id is not None:
                if pending_parent_id == account_id:
                    raise HTTPException(
                        status_code=400,
                        detail="Circular parent-child relationship is not allowed"
                    )

                if pending_parent_id in visited_ids:
                    raise HTTPException(
                        status_code=400,
                        detail="Existing circular hierarchy detected"
                    )

                visited_ids.add(pending_parent_id)

                ancestor = db.execute(
                    text("""
                        SELECT parent_account_id
                        FROM chart_of_accounts
                        WHERE id = :parent_id
                          AND company_code = :company_code
                    """),
                    {
                        "parent_id": pending_parent_id,
                        "company_code": current["company_code"]
                    }
                ).mappings().first()

                if not ancestor:
                    break

                pending_parent_id = ancestor["parent_account_id"]

    assignments = ", ".join(
        f"{field} = :{field}" for field in changes
    )

    params = {**changes, "id": account_id}

    try:
        db.execute(
            text(f"""
                UPDATE chart_of_accounts
                SET {assignments}
                WHERE id = :id
            """),
            params
        )
        db.commit()

    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Account update conflicts with a database constraint"
        ) from exc

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database operation failed"
        ) from exc

    return get_account(account_id, db)



@router.patch("/{account_id}/deactivate")
def deactivate_account(
    account_id: int,
    db: Session = Depends(get_db)
):
    result = db.execute(
        text("""
            UPDATE chart_of_accounts
            SET is_active = FALSE
            WHERE id = :id AND is_active = TRUE
        """),
        {"id": account_id}
    )

    if result.rowcount == 0:
        db.rollback()
        exists = db.execute(
            text("SELECT id FROM chart_of_accounts WHERE id = :id"),
            {"id": account_id}
        ).first()

        if not exists:
            raise HTTPException(status_code=404, detail="Account not found")

        raise HTTPException(status_code=409, detail="Account is already inactive")
    
@router.patch("/{account_id}/activate")
def activate_account(
    account_id: int,
    db: Session = Depends(get_db)
):
    # Check whether the account exists
    account = db.execute(
        text("""
            SELECT id, company_code, is_active
            FROM chart_of_accounts
            WHERE id = :id
        """),
        {"id": account_id}
    ).mappings().first()

    if not account:
        raise HTTPException(
            status_code=404,
            detail="Account not found"
        )

    # Prevent activating an already active account
    if account["is_active"]:
        raise HTTPException(
            status_code=409,
            detail="Account is already active"
        )

    try:
        db.execute(
            text("""
                UPDATE chart_of_accounts
                SET is_active = TRUE
                WHERE id = :id
            """),
            {"id": account_id}
        )

        db.commit()

    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Database operation failed"
        ) from exc

    return {
        "message": "Account activated successfully",
        "id": account_id,
        "company_code": account["company_code"],
        "is_active": True
    }
    db.commit()
    return {"message": "Account deactivated successfully", "id": account_id}


import mysql.connector
from mysql.connector import Error
from datetime import datetime
from decimal import Decimal, InvalidOperation


# ============================================================
# FICO JOURNAL ENTRY MANAGEMENT SYSTEM
# File: journal.py
# Database: fico_db
# ============================================================

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "fico_db"
}

MAX_AMOUNT = Decimal("9999999999999.99")


# ============================================================
# 1. DATABASE CONNECTION
# ============================================================

def get_connection():
    """Connect to the FICO MySQL database."""

    try:
        connection = mysql.connector.connect(**DB_CONFIG)

        if connection.is_connected():
            return connection

        connection.close()

    except Error as error:
        print("Database connection failed:", error)

    return None


# ============================================================
# 2. CREATE DATABASE TABLES
# ============================================================

def initialize_database():
    """Create journal tables if they do not already exist."""

    connection = get_connection()

    if connection is None:
        return False

    cursor = None

    try:
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                entry_id INT AUTO_INCREMENT PRIMARY KEY,
                document_number VARCHAR(30) UNIQUE,
                document_date DATE NOT NULL,
                posting_date DATE NOT NULL,
                company_code VARCHAR(10) NOT NULL,
                fiscal_year INT NOT NULL,
                currency VARCHAR(3) NOT NULL DEFAULT 'INR',
                reference_number VARCHAR(50),
                description VARCHAR(255) NOT NULL,
                status ENUM('DRAFT', 'POSTED')
                    NOT NULL DEFAULT 'DRAFT',
                total_debit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                total_credit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS journal_entry_lines (
                line_id INT AUTO_INCREMENT PRIMARY KEY,
                entry_id INT NOT NULL,
                line_number INT NOT NULL,
                account_code VARCHAR(20) NOT NULL,
                account_name VARCHAR(100) NOT NULL,
                line_description VARCHAR(255),
                debit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                credit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                cost_center VARCHAR(30),

                CONSTRAINT fk_journal_entry
                    FOREIGN KEY (entry_id)
                    REFERENCES journal_entries(entry_id)
                    ON DELETE CASCADE,

                UNIQUE KEY uq_entry_line (entry_id, line_number)
            ) ENGINE=InnoDB
        """)

        connection.commit()

        print("Database connected successfully.")
        print("Journal tables are ready.")

        return True

    except Error as error:
        connection.rollback()
        print("Table initialization failed:", error)
        return False

    finally:
        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# 3. INPUT VALIDATION HELPERS
# ============================================================

def get_required_text(message, maximum_length):
    while True:
        value = input(message).strip()

        if not value:
            print("This field is required.")

        elif len(value) > maximum_length:
            print(
                f"Maximum length is {maximum_length} characters."
            )

        else:
            return value


def get_optional_text(message, maximum_length):
    value = input(message).strip()

    if not value:
        return None

    if len(value) > maximum_length:
        print(
            f"Maximum length is {maximum_length} characters."
        )
        return None

    return value


def get_date(message):
    while True:
        value = input(message).strip()

        try:
            return datetime.strptime(
                value, "%Y-%m-%d"
            ).date()

        except ValueError:
            print("Enter the date in YYYY-MM-DD format.")


def get_amount(message):
    while True:
        try:
            value = Decimal(input(message).strip())

            if not value.is_finite():
                print("Enter a valid amount.")
                continue

            if value < 0 or value > MAX_AMOUNT:
                print("Amount is outside the allowed range.")
                continue

            if value.as_tuple().exponent < -2:
                print("Maximum two decimal places are allowed.")
                continue

            return value

        except (ValueError, InvalidOperation):
            print("Enter a valid amount, such as 1500.00.")


def get_positive_integer(message, minimum, maximum):
    while True:
        try:
            value = int(input(message).strip())

            if minimum <= value <= maximum:
                return value

            print(f"Enter a number from {minimum} to {maximum}.")

        except ValueError:
            print("Enter a valid whole number.")


# ============================================================
# 4. SHARED JOURNAL VALIDATION
# ============================================================

def validate_journal_data(
    document_date,
    posting_date,
    company_code,
    currency,
    reference_number,
    description,
    lines
):
    """Validate journal header, lines and debit-credit totals."""

    try:
        document_date = datetime.strptime(
            str(document_date), "%Y-%m-%d"
        ).date()

        posting_date = datetime.strptime(
            str(posting_date), "%Y-%m-%d"
        ).date()

    except ValueError:
        raise ValueError("Invalid document date or posting date.")

    if posting_date < document_date:
        raise ValueError(
            "Posting date cannot be earlier than document date."
        )

    company_code = str(company_code or "").strip()
    currency = str(currency or "").strip().upper()
    description = str(description or "").strip()
    reference_number = str(reference_number or "").strip() or None

    if not company_code or len(company_code) > 10:
        raise ValueError("Enter a valid company code.")

    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Currency must contain three letters.")

    if not description or len(description) > 255:
        raise ValueError("Enter a valid journal description.")

    if reference_number and len(reference_number) > 50:
        raise ValueError("Reference number is too long.")

    if not isinstance(lines, list) or not 2 <= len(lines) <= 500:
        raise ValueError(
            "A journal entry must contain between 2 and 500 lines."
        )

    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    validated_lines = []

    for number, line in enumerate(lines, start=1):

        account_code = str(
            line.get("account_code", "")
        ).strip()

        account_name = str(
            line.get("account_name", "")
        ).strip()

        line_description = str(
            line.get("line_description", "") or ""
        ).strip() or None

        cost_center = str(
            line.get("cost_center", "") or ""
        ).strip() or None

        if not account_code or len(account_code) > 20:
            raise ValueError(
                f"Line {number}: enter a valid account code."
            )

        if not account_name or len(account_name) > 100:
            raise ValueError(
                f"Line {number}: enter a valid account name."
            )

        if line_description and len(line_description) > 255:
            raise ValueError(
                f"Line {number}: description is too long."
            )

        if cost_center and len(cost_center) > 30:
            raise ValueError(
                f"Line {number}: cost center is too long."
            )

        try:
            debit = Decimal(str(line.get("debit", "0")))
            credit = Decimal(str(line.get("credit", "0")))

        except (ValueError, InvalidOperation):
            raise ValueError(
                f"Line {number}: enter valid debit and credit amounts."
            )

        if not debit.is_finite() or not credit.is_finite():
            raise ValueError(
                f"Line {number}: amounts must be valid numbers."
            )

        if debit < 0 or credit < 0:
            raise ValueError(
                f"Line {number}: negative amounts are not allowed."
            )

        if debit > MAX_AMOUNT or credit > MAX_AMOUNT:
            raise ValueError(
                f"Line {number}: amount exceeds the allowed limit."
            )

        if (
            debit.as_tuple().exponent < -2
            or credit.as_tuple().exponent < -2
        ):
            raise ValueError(
                f"Line {number}: maximum two decimal places are allowed."
            )

        if (debit > 0 and credit > 0) or (
            debit == 0 and credit == 0
        ):
            raise ValueError(
                f"Line {number}: enter either debit or credit, not both."
            )

        total_debit += debit
        total_credit += credit

        validated_lines.append({
            "line_number": number,
            "account_code": account_code,
            "account_name": account_name,
            "line_description": line_description,
            "debit": debit,
            "credit": credit,
            "cost_center": cost_center
        })

    if total_debit <= 0:
        raise ValueError("Total debit must be greater than zero.")

    if total_debit != total_credit:
        raise ValueError(
            "Journal entry is not balanced. "
            "Total debit must equal total credit."
        )

    return {
        "document_date": document_date,
        "posting_date": posting_date,
        "company_code": company_code,
        "fiscal_year": posting_date.year,
        "currency": currency,
        "reference_number": reference_number,
        "description": description,
        "lines": validated_lines,
        "total_debit": total_debit,
        "total_credit": total_credit
    }


# ============================================================
# 5. SHARED DATABASE SAVING FUNCTION
# ============================================================

def _save_validated_journal(data):
    """Save a validated journal header and lines atomically."""

    connection = get_connection()

    if connection is None:
        raise RuntimeError("Unable to connect to the MySQL database.")

    cursor = None

    try:
        connection.start_transaction()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO journal_entries (
                document_date,
                posting_date,
                company_code,
                fiscal_year,
                currency,
                reference_number,
                description,
                status,
                total_debit,
                total_credit
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                'DRAFT', %s, %s
            )
        """, (
            data["document_date"],
            data["posting_date"],
            data["company_code"],
            data["fiscal_year"],
            data["currency"],
            data["reference_number"],
            data["description"],
            data["total_debit"],
            data["total_credit"]
        ))

        entry_id = cursor.lastrowid
        document_number = f"JE{entry_id:08d}"

        cursor.execute("""
            UPDATE journal_entries
            SET document_number = %s
            WHERE entry_id = %s
        """, (document_number, entry_id))

        for line in data["lines"]:
            cursor.execute("""
                INSERT INTO journal_entry_lines (
                    entry_id,
                    line_number,
                    account_code,
                    account_name,
                    line_description,
                    debit,
                    credit,
                    cost_center
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                entry_id,
                line["line_number"],
                line["account_code"],
                line["account_name"],
                line["line_description"],
                line["debit"],
                line["credit"],
                line["cost_center"]
            ))

        connection.commit()

        return {
            "entry_id": entry_id,
            "document_number": document_number,
            "status": "DRAFT",
            "total_debit": data["total_debit"],
            "total_credit": data["total_credit"],
            "currency": data["currency"]
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# 6. FLASK WEB INTEGRATION
# ============================================================

def save_journal_entry_record(
    document_date,
    posting_date,
    company_code,
    currency,
    reference_number,
    description,
    lines
):
    """
    Public function for app.py.

    Accepts data from the Flask form, validates it,
    saves the header and lines, and returns the document details.
    """

    data = validate_journal_data(
        document_date,
        posting_date,
        company_code,
        currency,
        reference_number,
        description,
        lines
    )

    return _save_validated_journal(data)


# ============================================================
# 7. CREATE JOURNAL ENTRY FROM TERMINAL
# ============================================================

def create_journal_entry():
    print("\n" + "=" * 55)
    print("CREATE JOURNAL ENTRY")
    print("=" * 55)

    document_date = input(
        "Document date (YYYY-MM-DD): "
    ).strip()

    posting_date = input(
        "Posting date (YYYY-MM-DD): "
    ).strip()

    company_code = get_required_text("Company code: ", 10)

    currency = get_required_text(
        "Currency (example INR): ", 3
    ).upper()

    reference_number = get_optional_text(
        "Reference number (optional): ", 50
    )

    description = get_required_text(
        "Journal description: ", 255
    )

    line_count = get_positive_integer(
        "Number of journal lines (2-500): ", 2, 500
    )

    lines = []

    for number in range(1, line_count + 1):
        print(f"\n--- JOURNAL LINE {number} ---")

        account_code = get_required_text("Account code: ", 20)
        account_name = get_required_text("Account name: ", 100)

        line_description = get_optional_text(
            "Line description (optional): ", 255
        )

        debit = get_amount("Debit amount: ")
        credit = get_amount("Credit amount: ")

        cost_center = get_optional_text(
            "Cost center (optional): ", 30
        )

        lines.append({
            "account_code": account_code,
            "account_name": account_name,
            "line_description": line_description,
            "debit": debit,
            "credit": credit,
            "cost_center": cost_center
        })

    try:
        data = validate_journal_data(
            document_date,
            posting_date,
            company_code,
            currency,
            reference_number,
            description,
            lines
        )

        print("\n" + "-" * 55)
        print("JOURNAL ENTRY SUMMARY")
        print("-" * 55)
        print("Company code :", data["company_code"])
        print("Document date:", data["document_date"])
        print("Posting date :", data["posting_date"])
        print("Description  :", data["description"])
        print("Total debit  :", data["total_debit"], data["currency"])
        print("Total credit :", data["total_credit"], data["currency"])

        print("\nValidation successful. Debit equals credit.")

        confirmation = input(
            "Save journal entry as DRAFT? (yes/no): "
        ).strip().lower()

        if confirmation != "yes":
            print("Save cancelled.")
            return

        result = _save_validated_journal(data)

        print("\nSUCCESS: Journal entry saved successfully!")
        print("Document number:", result["document_number"])
        print("Entry ID:", result["entry_id"])
        print("Status:", result["status"])
        print("Total debit:", result["total_debit"], result["currency"])
        print("Total credit:", result["total_credit"], result["currency"])

    except (ValueError, RuntimeError) as error:
        print("\nJournal entry could not be saved:", error)

    except Error as error:
        print("\nDatabase error:", error)


# ============================================================
# 8. VIEW ALL JOURNAL ENTRIES
# ============================================================

def view_journal_entries():
    connection = get_connection()

    if connection is None:
        print("Database unavailable.")
        return

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                entry_id,
                document_number,
                document_date,
                posting_date,
                company_code,
                fiscal_year,
                currency,
                description,
                status,
                total_debit,
                total_credit,
                created_at
            FROM journal_entries
            ORDER BY entry_id DESC
        """)

        entries = cursor.fetchall()

        print("\n" + "=" * 75)
        print("ALL JOURNAL ENTRIES")
        print("=" * 75)

        if not entries:
            print("No journal entries found.")
            return

        for entry in entries:
            print("-" * 75)

            for key, value in entry.items():
                print(f"{key}: {value}")

        print("Total records:", len(entries))

    except Error as error:
        print("Unable to retrieve journal entries:", error)

    finally:
        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# 9. VIEW JOURNAL ENTRY DETAILS
# ============================================================

def view_journal_entry_details():
    document_number = get_required_text(
        "Enter document number: ", 30
    )

    connection = get_connection()

    if connection is None:
        return

    cursor = None

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM journal_entries
            WHERE document_number = %s
        """, (document_number,))

        header = cursor.fetchone()

        if not header:
            print("Journal entry not found.")
            return

        print("\nJOURNAL HEADER")

        for key, value in header.items():
            print(f"{key}: {value}")

        cursor.execute("""
            SELECT *
            FROM journal_entry_lines
            WHERE entry_id = %s
            ORDER BY line_number
        """, (header["entry_id"],))

        lines = cursor.fetchall()

        print("\nJOURNAL LINE ITEMS")

        for line in lines:
            print("-" * 50)

            for key, value in line.items():
                print(f"{key}: {value}")

    except Error as error:
        print("Unable to retrieve details:", error)

    finally:
        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# 10. POST JOURNAL ENTRY
# ============================================================

def post_journal_entry():
    document_number = get_required_text(
        "Enter document number to post: ", 30
    )

    connection = get_connection()

    if connection is None:
        return

    cursor = None

    try:
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM journal_entries
            WHERE document_number = %s
            FOR UPDATE
        """, (document_number,))

        entry = cursor.fetchone()

        if not entry:
            connection.rollback()
            print("Journal entry not found.")
            return

        if entry["status"] != "DRAFT":
            connection.rollback()
            print("Only DRAFT entries can be posted.")
            return

        cursor.execute("""
            SELECT
                COUNT(*) AS line_count,
                COALESCE(SUM(debit), 0) AS total_debit,
                COALESCE(SUM(credit), 0) AS total_credit,
                COALESCE(SUM(
                    CASE
                        WHEN (debit > 0 AND credit > 0)
                          OR (debit = 0 AND credit = 0)
                        THEN 1
                        ELSE 0
                    END
                ), 0) AS invalid_lines
            FROM journal_entry_lines
            WHERE entry_id = %s
        """, (entry["entry_id"],))

        totals = cursor.fetchone()

        debit = Decimal(str(totals["total_debit"]))
        credit = Decimal(str(totals["total_credit"]))

        if totals["line_count"] < 2:
            connection.rollback()
            print("Posting rejected: minimum two lines required.")
            return

        if totals["invalid_lines"] > 0:
            connection.rollback()
            print("Posting rejected: invalid journal line.")
            return

        if debit <= 0 or debit != credit:
            connection.rollback()
            print("Posting rejected: entry is not balanced.")
            return

        if (
            debit != Decimal(str(entry["total_debit"]))
            or credit != Decimal(str(entry["total_credit"]))
        ):
            connection.rollback()
            print("Posting rejected: header totals do not match.")
            return

        confirmation = input(
            f"Confirm posting {document_number}? (yes/no): "
        ).strip().lower()

        if confirmation != "yes":
            connection.rollback()
            print("Posting cancelled.")
            return

        cursor.execute("""
            UPDATE journal_entries
            SET status = 'POSTED'
            WHERE entry_id = %s AND status = 'DRAFT'
        """, (entry["entry_id"],))

        if cursor.rowcount != 1:
            connection.rollback()
            print("Posting failed. Please try again.")
            return

        connection.commit()
        print("Journal entry posted successfully.")

    except Error as error:
        connection.rollback()
        print("Posting failed:", error)

    finally:
        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# 11. DELETE DRAFT JOURNAL ENTRY
# ============================================================

def delete_draft_entry():
    document_number = get_required_text(
        "Enter document number to delete: ", 30
    )

    connection = get_connection()

    if connection is None:
        return

    cursor = None

    try:
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT entry_id, status
            FROM journal_entries
            WHERE document_number = %s
            FOR UPDATE
        """, (document_number,))

        entry = cursor.fetchone()

        if not entry:
            connection.rollback()
            print("Journal entry not found.")
            return

        if entry["status"] != "DRAFT":
            connection.rollback()
            print("Posted entries cannot be deleted.")
            return

        confirmation = input(
            "Permanently delete this draft? (yes/no): "
        ).strip().lower()

        if confirmation != "yes":
            connection.rollback()
            print("Deletion cancelled.")
            return

        cursor.execute("""
            DELETE FROM journal_entries
            WHERE entry_id = %s AND status = 'DRAFT'
        """, (entry["entry_id"],))

        if cursor.rowcount != 1:
            connection.rollback()
            print("Deletion failed.")
            return

        connection.commit()
        print("Draft journal entry deleted successfully.")

    except Error as error:
        connection.rollback()
        print("Deletion failed:", error)

    finally:
        if cursor is not None:
            cursor.close()

        connection.close()


# ============================================================
# 12. MAIN MENU
# ============================================================

def main():
    print("=" * 60)
    print("FICO JOURNAL ENTRY MANAGEMENT SYSTEM")
    print("=" * 60)

    if not initialize_database():
        print("Cannot start. Check the MySQL database settings.")
        return

    while True:
        print("\n" + "=" * 40)
        print("MAIN MENU")
        print("=" * 40)
        print("1. Create Journal Entry")
        print("2. View All Journal Entries")
        print("3. View Journal Entry Details")
        print("4. Post Journal Entry")
        print("5. Delete Draft Journal Entry")
        print("6. Exit")

        choice = input("Enter your choice (1-6): ").strip()

        if choice == "1":
            create_journal_entry()

        elif choice == "2":
            view_journal_entries()

        elif choice == "3":
            view_journal_entry_details()

        elif choice == "4":
            post_journal_entry()

        elif choice == "5":
            delete_draft_entry()

        elif choice == "6":
            print("Thank you for using the FICO Journal Entry System.")
            break

        else:
            print("Invalid choice. Enter a number from 1 to 6.")


# ============================================================
# 13. PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()

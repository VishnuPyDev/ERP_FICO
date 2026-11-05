
-- =========================================================
-- JOURNAL ENTRY MANAGEMENT SYSTEM - FICO MODULE
-- DATABASE: erp_fico
-- MySQL Workbench
-- =========================================================

CREATE DATABASE IF NOT EXISTS erp_fico;

USE erp_fico;

-- =========================================================
-- 1. JOURNAL ENTRIES (HEADER TABLE)
-- =========================================================

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

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_total_debit
        CHECK (total_debit >= 0),

    CONSTRAINT chk_total_credit
        CHECK (total_credit >= 0),

    INDEX idx_document_date (document_date),
    INDEX idx_posting_date (posting_date),
    INDEX idx_company_code (company_code),
    INDEX idx_journal_status (status),
    INDEX idx_fiscal_year (fiscal_year)
);

-- =========================================================
-- 2. JOURNAL ENTRY LINES (LINE ITEMS TABLE)
-- =========================================================

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

    CONSTRAINT fk_journal_entry_lines
        FOREIGN KEY (entry_id)
        REFERENCES journal_entries(entry_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_entry_line
        UNIQUE (entry_id, line_number),

    CONSTRAINT chk_line_debit
        CHECK (debit >= 0),

    CONSTRAINT chk_line_credit
        CHECK (credit >= 0),

    CONSTRAINT chk_debit_or_credit
        CHECK (
            (debit > 0 AND credit = 0)
            OR
            (credit > 0 AND debit = 0)
        ),

    INDEX idx_account_code (account_code)
);

-- =========================================================
-- 3. VERIFY TABLES
-- =========================================================

SHOW TABLES;

DESCRIBE journal_entries;

DESCRIBE journal_entry_lines;

-- =========================================================
-- 4. VIEW JOURNAL ENTRY HEADERS
-- =========================================================

SELECT *
FROM journal_entries
ORDER BY entry_id DESC;

-- =========================================================
-- 5. VIEW JOURNAL ENTRY LINE ITEMS
-- =========================================================

SELECT *
FROM journal_entry_lines
ORDER BY entry_id, line_number;

-- =========================================================
-- 6. VIEW COMPLETE JOURNAL ENTRIES
-- =========================================================

SELECT
    je.document_number,
    je.document_date,
    je.posting_date,
    je.company_code,
    je.fiscal_year,
    je.currency,
    je.description,
    je.status,
    jel.line_number,
    jel.account_code,
    jel.account_name,
    jel.line_description,
    jel.debit,
    jel.credit,
    jel.cost_center
FROM journal_entries je
LEFT JOIN journal_entry_lines jel
    ON je.entry_id = jel.entry_id
ORDER BY je.entry_id, jel.line_number;

-- =========================================================
-- 7. CHECK DEBIT AND CREDIT TOTALS
-- =========================================================

SELECT
    je.document_number,
    je.status,
    COALESCE(SUM(jel.debit), 0.00) AS total_debit,
    COALESCE(SUM(jel.credit), 0.00) AS total_credit,
    CASE
        WHEN COUNT(jel.line_id) < 2
            THEN 'INVALID: MINIMUM 2 LINES REQUIRED'
        WHEN COALESCE(SUM(jel.debit), 0.00)
           = COALESCE(SUM(jel.credit), 0.00)
         AND COALESCE(SUM(jel.debit), 0.00) > 0
            THEN 'BALANCED'
        ELSE 'NOT BALANCED'
    END AS balance_status
FROM journal_entries je
LEFT JOIN journal_entry_lines jel
    ON je.entry_id = jel.entry_id
GROUP BY
    je.entry_id,
    je.document_number,
    je.status
ORDER BY je.entry_id DESC;

-- =========================================================
-- 8. VIEW DRAFT ENTRIES
-- =========================================================

SELECT *
FROM journal_entries
WHERE status = 'DRAFT';

-- =========================================================
-- 9. VIEW POSTED ENTRIES
-- =========================================================

SELECT *
FROM journal_entries
WHERE status = 'POSTED';

-- =========================================================
-- 10. SEARCH BY DOCUMENT NUMBER
-- Replace JE00000001 with your document number
-- =========================================================

SELECT
    je.*,
    jel.line_number,
    jel.account_code,
    jel.account_name,
    jel.debit,
    jel.credit
FROM journal_entries je
LEFT JOIN journal_entry_lines jel
    ON je.entry_id = jel.entry_id
WHERE je.document_number = 'JE00000001'
ORDER BY jel.line_number;

SHOW TABLES;


USE erp_fico;

SELECT * FROM journal_entries;

SELECT * FROM journal_entry_lines;


-- =========================================================
-- END OF SQL SCRIPT
-- =========================================================

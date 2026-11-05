
CREATE DATABASE IF NOT EXISTS erp_fico;

USE erp_fico;

CREATE TABLE chart_of_accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_code VARCHAR(20) NOT NULL,
    account_code VARCHAR(20) NOT NULL,
    account_name VARCHAR(100) NOT NULL,

    account_type ENUM(
        'ASSET',
        'LIABILITY',
        'EQUITY',
        'REVENUE',
        'EXPENSE'
    ) NOT NULL,

    parent_account_id INT NULL,
    normal_balance ENUM('DEBIT', 'CREDIT') NOT NULL,
    description VARCHAR(255) NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_company_account
        UNIQUE (company_code, account_code),

    CONSTRAINT fk_parent_account
        FOREIGN KEY (parent_account_id)
        REFERENCES chart_of_accounts(id)
);


SELECT
    id,
    company_code,
    customer_id,
    invoice_number,
    total_amount,
    status
FROM ar_invoices
ORDER BY id;

SELECT
    company_code,
    COUNT(*) AS total_invoices
FROM ar_invoices
GROUP BY company_code;
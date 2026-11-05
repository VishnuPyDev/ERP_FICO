
USE erp_fico;

CREATE TABLE IF NOT EXISTS ar_invoices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_code VARCHAR(20) NOT NULL,
    customer_id INT NOT NULL,
    invoice_number VARCHAR(40) NOT NULL,
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    status ENUM('DRAFT', 'POSTED', 'CANCELLED')
        NOT NULL DEFAULT 'DRAFT',
    subtotal DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    tax_amount DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    total_amount DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    notes TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_ar_invoice_number
        UNIQUE (company_code, invoice_number),

    CONSTRAINT fk_ar_invoice_customer
        FOREIGN KEY (customer_id)
        REFERENCES ar_customers(id),

    CONSTRAINT chk_ar_invoice_totals
        CHECK (
            subtotal >= 0
            AND tax_amount >= 0
            AND total_amount >= 0
        ),

    INDEX idx_ar_invoice_customer (company_code, customer_id),
    INDEX idx_ar_invoice_due_date (company_code, due_date)
);

CREATE TABLE IF NOT EXISTS ar_invoice_lines (
    id INT AUTO_INCREMENT PRIMARY KEY,
    invoice_id INT NOT NULL,
    description VARCHAR(255) NOT NULL,
    quantity DECIMAL(15,3) NOT NULL,
    unit_price DECIMAL(15,2) NOT NULL,
    tax_rate DECIMAL(5,2) NOT NULL DEFAULT 0.00,
    line_subtotal DECIMAL(15,2) NOT NULL,
    tax_amount DECIMAL(15,2) NOT NULL,
    line_total DECIMAL(15,2) NOT NULL,

    CONSTRAINT fk_ar_invoice_line_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES ar_invoices(id)
        ON DELETE CASCADE,

    CONSTRAINT chk_ar_invoice_line_values
        CHECK (
            quantity > 0
            AND unit_price >= 0
            AND tax_rate >= 0
            AND tax_rate <= 100
        )
);
DESCRIBE ar_invoices;
DESCRIBE ar_customers;
describe accounts;

SELECT id, company_code, customer_code, customer_name, is_active
FROM ar_customers
WHERE company_code = 'STK026';

SELECT * FROM ar_invoices;
SELECT * FROM ar_invoice_lines;

SELECT User, Host
FROM mysql.user
WHERE User IN ('root', 'fico_user');

USE erp_fico;
show tables;

SELECT *
FROM ar_invoices;

SELECT *
FROM ar_payments
WHERE payment_reference = 'PAY-TEST-001';
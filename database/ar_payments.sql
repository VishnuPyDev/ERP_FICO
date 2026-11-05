USE erp_fico;

CREATE TABLE IF NOT EXISTS ar_payments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    company_code VARCHAR(20) NOT NULL,
    invoice_id INT NOT NULL,
    payment_reference VARCHAR(50) NOT NULL,
    payment_date DATE NOT NULL,
    amount DECIMAL(15, 2) NOT NULL,
    payment_method VARCHAR(30) NOT NULL,
    notes VARCHAR(500) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_ar_payment_amount
        CHECK (amount > 0),

    CONSTRAINT uq_ar_payment_reference
        UNIQUE (company_code, payment_reference),

    CONSTRAINT fk_ar_payment_invoice
        FOREIGN KEY (invoice_id)
        REFERENCES ar_invoices(id),

    INDEX idx_ar_payment_invoice (invoice_id),
    INDEX idx_ar_payment_company (company_code)
);

DESCRIBE ar_payments;

SELECT
    i.id AS invoice_id,
    i.company_code,
    i.invoice_number,
    i.total_amount,
    COALESCE(SUM(p.amount), 0) AS amount_paid,
    i.total_amount - COALESCE(SUM(p.amount), 0) AS outstanding_balance
FROM ar_invoices i
LEFT JOIN ar_payments p
    ON p.invoice_id = i.id
   AND p.company_code = i.company_code
WHERE i.id = 1
GROUP BY
    i.id,
    i.company_code,
    i.invoice_number,
    i.total_amount;
    
    SELECT *
FROM ar_payments
ORDER BY id DESC;

USE erp_fico;

SELECT *
FROM ar_customers
WHERE company_code = 'STK026';

SELECT id, company_code, customer_code, customer_name, is_active
FROM ar_customers
WHERE company_code = 'STK026'
  AND customer_code = 'CUST002';
  
SELECT company_code, customer_code, COUNT(*) AS total
FROM ar_customers
GROUP BY company_code, customer_code
HAVING COUNT(*) > 1; 
SHOW CREATE TABLE ar_customers;

SELECT id, company_code, customer_code, customer_name,
       email, phone, is_active
FROM ar_customers
WHERE company_code = 'STK026'
ORDER BY id;

SELECT
    id,
    company_code,
    customer_code,
    customer_name,
    is_active
FROM ar_customers
WHERE company_code = 'STK026'
ORDER BY id;

SHOW INDEX FROM ar_customers

SELECT DATABASE();
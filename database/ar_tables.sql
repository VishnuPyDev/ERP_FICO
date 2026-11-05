
USE erp_fico;

CREATE TABLE IF NOT EXISTS ar_customers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_code VARCHAR(20) NOT NULL,
    customer_code VARCHAR(30) NOT NULL,
    customer_name VARCHAR(150) NOT NULL,
    email VARCHAR(150),
    phone VARCHAR(20),
    address TEXT,
    payment_terms_days INT NOT NULL DEFAULT 30,
    credit_limit DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_ar_customer_code
        UNIQUE (company_code, customer_code),

    CONSTRAINT chk_payment_terms
        CHECK (payment_terms_days >= 0),

    CONSTRAINT chk_credit_limit
        CHECK (credit_limit >= 0)
);

DESCRIBE ar_customers;
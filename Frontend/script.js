
const API_BASE = "http://127.0.0.1:8000";

const message = document.getElementById("message");
const accountTree = document.getElementById("accountTree");
let allAccounts = [];
const addAccountForm = document.getElementById("addAccountForm");
const formMessage = document.getElementById("formMessage");
const refreshBtn = document.getElementById("refreshBtn");

// Clear old messages whenever the Add Account form is opened
document.getElementById("addAccountBtn").addEventListener("click", () => {
    formMessage.textContent = "";
    formMessage.className = "d-none";
});
// Load accounts when the user clicks the Load button.
document.getElementById("loadAccountsBtn")
    .addEventListener("click", loadAccounts);
// Refresh the latest accounts from the backend.
document.getElementById("refreshBtn")
    .addEventListener("click", loadAccounts);
// Automatically load Chart of Accounts when the page opens
document.addEventListener("DOMContentLoaded", async function () {
    await loadAccounts();
});
async function loadAccounts() {
    const companyCode = document
        .getElementById("companyCode")
        .value.trim();

    const refreshButton = document.getElementById("refreshBtn");

    if (!companyCode) {
        message.textContent = "Please enter a company code.";
        message.className = "alert alert-warning";
        return;
    }

    if (refreshButton) {
        refreshButton.disabled = true;
        refreshButton.textContent = "Refreshing...";
    }

    message.textContent = "Loading latest accounts...";
    message.className = "alert alert-info";

    try {
        const response = await fetch(
            `${API_BASE}/chart-of-accounts/hierarchy/${encodeURIComponent(companyCode)}?t=${Date.now()}`
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.detail || "Unable to load accounts."
            );
        }

        // Store the latest account data received from the backend.
        allAccounts = flattenAccounts(data.accounts || []);

        // Update summary counts.
        document.getElementById("totalAccounts").textContent =
            allAccounts.length;

        document.getElementById("activeAccounts").textContent =
            allAccounts.filter(account =>
                account.is_active === true ||
                account.is_active === 1
            ).length;

        document.getElementById("inactiveAccounts").textContent =
            allAccounts.filter(account =>
                account.is_active === false ||
                account.is_active === 0
            ).length;

        // Render the latest data using the current search and filters.
        renderAccounts();

        message.textContent =
            `Successfully loaded ${allAccounts.length} accounts.`;

        message.className = "alert alert-success";

    } catch (error) {
        console.error("Error loading Chart of Accounts:", error);

        message.textContent =
            error.message ||
            "Unable to load accounts. Check the backend connection.";

        message.className = "alert alert-danger";

    } finally {
        if (refreshButton) {
            refreshButton.disabled = false;
            refreshButton.textContent = "Refresh";
        }
    }
}

function renderAccounts() {
    const searchText =
        document.getElementById("searchAccount").value
            .trim().toLowerCase();

    const selectedType =
        document.getElementById("filterType").value;

    const selectedStatus =
        document.getElementById("filterStatus").value;

    const filteredAccounts = allAccounts.filter(account => {
        const matchesSearch =
            account.account_code.toLowerCase().includes(searchText) ||
            account.account_name.toLowerCase().includes(searchText);

        const matchesType =
            !selectedType || account.account_type === selectedType;

        const matchesStatus =
            !selectedStatus ||
            (selectedStatus === "active" && Boolean(account.is_active)) ||
            (selectedStatus === "inactive" && !Boolean(account.is_active));

        return matchesSearch && matchesType && matchesStatus;
    });

    accountTree.replaceChildren();

    if (filteredAccounts.length === 0) {
        accountTree.textContent = "No matching accounts found.";
        return;
    }

    filteredAccounts.forEach(account => {
        accountTree.appendChild(createNode(account));
    });
}
function selectSearchResult(entry) {
    searchResults.classList.remove("show");

    if (entry.type === "transaction") {
        showHome();

        document.querySelectorAll("#transactionRows tr")
            .forEach(row => row.classList.remove("search-match"));

        entry.row.classList.add("search-match");

        entry.row.scrollIntoView({
            behavior: "smooth",
            block: "center"
        });

        return;
    }

    openModule(entry.key);
}

document.getElementById("searchAccount")
    .addEventListener("input", renderAccounts);

document.getElementById("filterType")
    .addEventListener("change", renderAccounts);

document.getElementById("filterStatus")
    .addEventListener("change", renderAccounts);


function flattenAccounts(accounts) {
    return accounts.flatMap(account => [
        account,
        ...flattenAccounts(account.children || [])
    ]);
}


function createNode(account) {
    const node = document.createElement("div");
    node.className = "tree-node";

    const row = document.createElement("div");
    row.className = "account-row";

    const heading = document.createElement("div");
    heading.className = "account-name";
    heading.textContent =
        `${account.account_code} — ${account.account_name}`;

    const details = document.createElement("div");
    details.className = "account-details";
    details.textContent =
        `Type: ${account.account_type} | ` +
        `Status: ${account.is_active ? "Active" : "Inactive"}`;

    const actions = document.createElement("div");
    actions.className = "mt-2 d-flex gap-2";

    const editButton = document.createElement("button");
    editButton.className = "btn btn-sm btn-outline-primary";
    editButton.textContent = "Edit";
    editButton.addEventListener("click", () => editAccount(account));

    actions.appendChild(editButton);

    if (account.is_active) {
        const deactivateButton = document.createElement("button");
        deactivateButton.className = "btn btn-sm btn-outline-danger";
        deactivateButton.textContent = "Deactivate";
        deactivateButton.addEventListener("click",
            () => deactivateAccount(account));

        actions.appendChild(deactivateButton);
    }

    row.append(heading, details, actions);
    node.appendChild(row);

    if (account.children && account.children.length > 0) {
        const children = document.createElement("div");
        children.className = "tree-children";

        account.children.forEach(child => {
            children.appendChild(createNode(child));
        });

        node.appendChild(children);
    }

    return node;
}

addAccountForm.addEventListener("submit", async function (event) {
    event.preventDefault();

    const parentValue =
        document.getElementById("parentAccountId").value.trim();

    const payload = {
        company_code:
            document.getElementById("formCompanyCode").value.trim(),
        account_code:
            document.getElementById("accountCode").value.trim(),
        account_name:
            document.getElementById("accountName").value.trim(),
        account_type:
            document.getElementById("accountType").value,
        parent_account_id: parentValue ? Number(parentValue) : null,
        normal_balance:
            document.getElementById("normalBalance").value,
        description:
            document.getElementById("accountDescription").value.trim()
    };

    
    formMessage.textContent = "Saving account...";
    formMessage.className = "alert alert-info";

    try {
        const response = await fetch(
            `${API_BASE}/chart-of-accounts/`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            }
        );

        const result = await response.json();

        if (!response.ok) {
            const detail = result.detail;
            throw new Error(
                typeof detail === "string"
                    ? detail
                    : JSON.stringify(detail || result)
            );
        }

        formMessage.textContent = "Account created successfully.";
        formMessage.className = "alert alert-success";

        addAccountForm.reset();
        formMessage.textContent = "";
        formMessage.className = "d-none";

        document.getElementById("companyCode").value =
            payload.company_code;

        await loadAccounts();

        setTimeout(() => {
            const modalElement =
                document.getElementById("addAccountModal");
            bootstrap.Modal.getOrCreateInstance(modalElement).hide();
        }, 500);

    } catch (error) {
        formMessage.textContent = "Unable to save: " + error.message;
         formMessage.className = "alert alert-danger";
    }
});


function editAccount(account) {
    document.getElementById("editAccountId").value = account.id;
    document.getElementById("editAccountCode").value =
        account.account_code ?? "";
    document.getElementById("editAccountName").value =
        account.account_name ?? "";
    document.getElementById("editAccountType").value =
        account.account_type ?? "ASSET";
    document.getElementById("editNormalBalance").value =
        account.normal_balance ?? "DEBIT";
    document.getElementById("editParentAccountId").value =
        account.parent_account_id ?? "";
    document.getElementById("editDescription").value =
        account.description ?? "";

    const message = document.getElementById("editAccountMessage");
    message.textContent = "";
    message.className = "alert d-none mt-3 mb-0";

    const modalElement = document.getElementById("editAccountModal");
    const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    modal.show();
}

document.getElementById("editAccountForm")
    .addEventListener("submit", async function (event) {
        event.preventDefault();

        const accountId =
            document.getElementById("editAccountId").value;

        const parentValue =
            document.getElementById("editParentAccountId").value.trim();

        const payload = {
            account_name:
                document.getElementById("editAccountName").value.trim(),
            account_type:
                document.getElementById("editAccountType").value,
            normal_balance:
                document.getElementById("editNormalBalance").value,
            parent_account_id:
                parentValue === "" ? null : Number(parentValue),
            description:
                document.getElementById("editDescription").value.trim()
        };

        const message =
            document.getElementById("editAccountMessage");
        const saveButton =
            document.getElementById("saveAccountChangesBtn");

        if (!payload.account_name) {
            message.textContent = "Account name is required.";
            message.className = "alert alert-warning mt-3 mb-0";
            return;
        }

        if (
            parentValue !== "" &&
            (!Number.isInteger(Number(parentValue)) ||
             Number(parentValue) < 1)
        ) {
            message.textContent =
                "Parent Account ID must be a positive whole number.";
            message.className = "alert alert-warning mt-3 mb-0";
            return;
        }

        saveButton.disabled = true;
        saveButton.textContent = "Saving...";

        try {
            const response = await fetch(
                `${API_BASE}/chart-of-accounts/${accountId}`,
                {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify(payload)
                }
            );

            const result = await response.json().catch(() => ({}));

            if (!response.ok) {
                const detail = result.detail;
                throw new Error(
                    typeof detail === "string"
                        ? detail
                        : JSON.stringify(detail || result) ||
                          `Request failed (${response.status})`
                );
            }

            const modalElement =
                document.getElementById("editAccountModal");
            bootstrap.Modal.getOrCreateInstance(modalElement).hide();

            await loadAccounts();

        } catch (error) {
            message.textContent = "Unable to update account: " +
                error.message;
            message.className = "alert alert-danger mt-3 mb-0";
        } finally {
            saveButton.disabled = false;
            saveButton.textContent = "Save Changes";
        }
    });
function confirmAccountStatusChange(account, action) {
    return new Promise(resolve => {
        const modal = $("statusModal");
        const icon = $("statusModalIcon");
        const title = $("statusModalTitle");
        const message = $("statusModalMessage");
        const code = $("modalAccountCode");
        const name = $("modalAccountName");
        const actionLabel = $("modalAction");
        const cancelBtn = $("cancelStatusBtn");
        const confirmBtn = $("confirmStatusBtn");

        const isActivate = action === "activate";

        title.textContent = isActivate
            ? "Activate Account"
            : "Deactivate Account";

        message.textContent = isActivate
            ? "This account will become active and available for normal use."
            : "This account will become inactive. Review the account details before continuing.";

        code.textContent = account.account_code ?? "—";
        name.textContent = account.account_name ?? "—";
        actionLabel.textContent = isActivate ? "Activate" : "Deactivate";

        icon.textContent = isActivate ? "✓" : "!";
        icon.classList.toggle("activate", isActivate);

        confirmBtn.textContent = isActivate
            ? "Activate Account"
            : "Deactivate Account";

        confirmBtn.classList.toggle("activate", isActivate);

        modal.hidden = false;

        function cleanup(result) {
            modal.hidden = true;

            confirmBtn.removeEventListener("click", onConfirm);
            cancelBtn.removeEventListener("click", onCancel);
            modal.removeEventListener("click", onBackdrop);
            document.removeEventListener("keydown", onKeyDown);

            resolve(result);
        }

        function onConfirm() {
            cleanup(true);
        }

        function onCancel() {
            cleanup(false);
        }

        function onBackdrop(event) {
            if (event.target === modal) {
                cleanup(false);
            }
        }

        function onKeyDown(event) {
            if (event.key === "Escape") {
                cleanup(false);
            }
        }

        confirmBtn.addEventListener("click", onConfirm);
        cancelBtn.addEventListener("click", onCancel);
        modal.addEventListener("click", onBackdrop);
        document.addEventListener("keydown", onKeyDown);

        cancelBtn.focus();
    });
}
async function deactivateAccount(a) {
    const confirmed = await confirmAccountStatusChange(
        a,
        "deactivate"
    );

    if (!confirmed) {
        return;
    }

    try {
        const result = await api(
            "/chart-of-accounts/" + a.id + "/deactivate",
            {
                method: "PATCH"
            }
        );

        notify(
            result.message || "Account deactivated successfully.",
            "success"
        );

        await loadAccounts();

    } catch (error) {
        notify(
            "Failed to deactivate account.\n" + error.message,
            "error"
        );
    }
}
async function activateAccount(a) {
    const confirmed = await confirmAccountStatusChange(
        a,
        "activate"
    );

    if (!confirmed) {
        return;
    }

    try {
        const result = await api(
            "/chart-of-accounts/" + a.id + "/activate",
            {
                method: "PATCH"
            }
        );

        notify(
            result.message || "Account activated successfully.",
            "success"
        );

        await loadAccounts();

    } catch (error) {
        notify(
            "Failed to activate account.\n" + error.message,
            "error"
        );
    }
}

function formatAmount(value) {
    return Number(value || 0).toLocaleString("en-IN", {
        style: "currency",
        currency: "INR",
        minimumFractionDigits: 2
    });
}

function statusBadge(status) {
    const value = String(status || "").toUpperCase();

    let cssClass = "status-pending";

    if (value === "PAID" || value === "SUCCESS" ||
        value === "SUCCESSFUL") {
        cssClass = "status-paid";
    } else if (value === "PARTIALLY_PAID") {
        cssClass = "status-partial";
    } else if (value === "ERROR" || value === "FAILED") {
        cssClass = "status-error";
    }

    const badge = document.createElement("span");
    badge.className = "status-badge " + cssClass;
    badge.textContent = value || "UNKNOWN";

    return badge;
}

function showTableMessage(tbodyId, columnCount, message) {
    const tbody = document.getElementById(tbodyId);
    tbody.replaceChildren();

    const row = tbody.insertRow();
    const cell = row.insertCell();

    cell.colSpan = columnCount;
    cell.textContent = message;
}

function getCompanyCode() {
    // Uses the company-code field in your existing payment form.
    const input = document.getElementById("companyCode");

    if (!input || !input.value.trim()) {
        throw new Error("Enter a company code in the payment form first.");
    }

    return input.value.trim();
}

async function loadPaymentHistory() {
    const tbody = document.getElementById("paymentHistoryBody");

    if (!tbody) {
        console.error("Payment History table body not found.");
        return;
    }

    // Read the company code from the payment form.
    // If the field is empty, use the company code you are testing.
    const companyInput = document.getElementById("companyCode");
    const companyCode = companyInput?.value.trim() || "STK026";

    tbody.innerHTML = `
        <tr>
            <td colspan="8">Loading payment history...</td>
        </tr>
    `;

    try {
        const url =
            `${API_BASE}/ar/payments/?company_code=` +
            encodeURIComponent(companyCode);

        const response = await fetch(url);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                typeof data.detail === "string"
                    ? data.detail
                    : JSON.stringify(data.detail || data)
            );
        }

        // Support common response formats from FastAPI.
        let payments = [];

        if (Array.isArray(data)) {
            payments = data;
        } else if (Array.isArray(data.payments)) {
            payments = data.payments;
        } else if (Array.isArray(data.items)) {
            payments = data.items;
        } else if (Array.isArray(data.data)) {
            payments = data.data;
        }

        tbody.replaceChildren();

        if (payments.length === 0) {
            const row = tbody.insertRow();
            const cell = row.insertCell();
            cell.colSpan = 8;
            cell.textContent =
                `No payment records found for company ${companyCode}.`;
            return;
        }

        payments.forEach(payment => {
            const row = tbody.insertRow();

            const values = [
                payment.id ?? payment.payment_id ?? "—",
                payment.company_code ?? companyCode,
                payment.invoice_id ?? "—",
                payment.payment_reference ?? "—",
                payment.payment_date ?? "—",
                formatPaymentAmount(payment.amount),
                payment.payment_method ?? "—"
            ];

            values.forEach(value => {
                const cell = row.insertCell();
                cell.textContent = String(value);
            });

            const resultCell = row.insertCell();
            const badge = document.createElement("span");

            badge.className = "status-badge status-paid";
            badge.textContent = "RECORDED";

            resultCell.appendChild(badge);
        });

    } catch (error) {
        console.error("Payment History Error:", error);

        tbody.replaceChildren();

        const row = tbody.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 8;
        cell.textContent = `Could not load payments: ${error.message}`;
    }
}

// Format amounts safely without requiring another helper function.
function formatPaymentAmount(value) {
    const amount = Number(value);

    if (value === null || value === undefined || value === "" ||
        !Number.isFinite(amount)) {
        return "—";
    }

    return amount.toLocaleString("en-IN", {
        style: "currency",
        currency: "INR",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}


async function loadOutstandingInvoices() {
    const tbody = document.getElementById("outstandingInvoicesBody");

    try {
        const companyCode = getCompanyCode();

        showTableMessage(
            "outstandingInvoicesBody",
            6,
            "Loading outstanding invoices..."
        );

        const response = await fetch(
            `${API_BASE}/ar/payments/outstanding?company_code=${encodeURIComponent(companyCode)}`
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                data.detail || "Unable to retrieve outstanding invoices."
            );
        }

        const invoices = Array.isArray(data)
            ? data
            : data.invoices || data.items || data.data || [];

        tbody.replaceChildren();

        if (invoices.length === 0) {
            showTableMessage(
                "outstandingInvoicesBody",
                6,
                "No outstanding invoices. All eligible invoices are paid."
            );
            return;
        }

        invoices.forEach(invoice => {
            const row = tbody.insertRow();

            const values = [
                invoice.invoice_id,
                invoice.invoice_number,
                formatAmount(invoice.invoice_amount),
                formatAmount(invoice.total_paid),
                formatAmount(invoice.outstanding_amount)
            ];

            values.forEach(value => {
                const cell = row.insertCell();
                cell.textContent = value ?? "—";
            });

            const statusCell = row.insertCell();
            statusCell.appendChild(
                statusBadge(invoice.payment_status || "UNPAID")
            );
        });
    } catch (error) {
        showTableMessage(
            "outstandingInvoicesBody",
            6,
            "Could not load outstanding invoices: " + error.message
        );
    }
}
async function loadPaymentTracking() {
    const tbody = document.getElementById("paymentTrackingBody");

    if (!tbody) {
        console.error(
            "Payment Tracking table not found. Check the HTML element ID."
        );
        return;
    }

    const companyCode =
        document.getElementById("companyCode")?.value.trim() || "STK026";

    tbody.innerHTML = `
        <tr>
            <td colspan="8">Loading payment tracking...</td>
        </tr>
    `;

    try {
        const url =
            `${API_BASE}/ar/workflow/tracking?company_code=` +
            encodeURIComponent(companyCode);

        const response = await fetch(url);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(
                typeof data.detail === "string"
                    ? data.detail
                    : JSON.stringify(data.detail || data)
            );
        }

        const invoices = Array.isArray(data.items) ? data.items : [];

        if (invoices.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8">
                        No invoices found for company ${companyCode}.
                    </td>
                </tr>
            `;
            return;
        }

        tbody.replaceChildren();

        invoices.forEach(invoice => {
            const row = tbody.insertRow();

            const amount = Number(invoice.total_amount || 0);
            const paid = Number(invoice.total_paid || 0);
            const balance = Number(invoice.balance || 0);
            const status = invoice.payment_status || "PENDING";

            const values = [
                invoice.invoice_id,
                invoice.invoice_number,
                invoice.customer_id,
                formatCurrency(amount),
                formatCurrency(paid),
                formatCurrency(balance),
                invoice.due_date || "—"
            ];

            values.forEach(value => {
                const cell = row.insertCell();
                cell.textContent = value ?? "—";
            });

            const statusCell = row.insertCell();
            statusCell.appendChild(statusBadge(status));
        });

    } catch (error) {
        console.error("Payment tracking error:", error);

        tbody.innerHTML = `
            <tr>
                <td colspan="8">
                    Unable to load payment tracking: ${error.message}
                </td>
            </tr>
        `;
    }
}

function formatCurrency(value) {
    return new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        minimumFractionDigits: 2
    }).format(value);
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[character]);
}

function showPaymentTrackingMessage(message) {
    const tbody = document.getElementById("paymentTrackingBody");
    tbody.replaceChildren();

    const row = tbody.insertRow();
    const cell = row.insertCell();

    cell.colSpan = 8;
    cell.textContent = message;
}

function formatTrackingCurrency(value) {
    return Number(value || 0).toLocaleString("en-IN", {
        style: "currency",
        currency: "INR",
        minimumFractionDigits: 2
    });
}
function getPaymentCompanyCode() {
    const input = document.getElementById("company_code");
    const companyCode = input?.value.trim();

    if (!companyCode) {
        throw new Error("Please enter the company code first.");
    }

    return companyCode;
}
document.addEventListener("DOMContentLoaded", () => {
    loadPaymentTracking();
});

if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
        await loadAccounts();
    });
}
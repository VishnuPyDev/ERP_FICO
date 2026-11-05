from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from database import engine
from models import Base
# Authentication router
from auth import router as auth_router


# Existing routers
from dashboard import router as dashboard_router
from coa import router as coa_router
from ar_customers import router as ar_customers_router
from ar_invoices import router as ar_invoices_router
from ar_payments import router as ar_payments_router
from ar_workflow_router import router as ar_workflow_router
from financial_reports import router as financial_reports_router
from company_setup import router as company_setup_router
from ap import router as ap_router
from fiscal_calendar import router as fiscal_calendar_router
from journal_entry import router as journal_entry_router
from general_ledger import router as general_ledger_router
from fixed_assets import router as fixed_assets_router
from budgeting import router as budgeting_router
from gst import router as gst_router




app = FastAPI(title="STACKLY OneERP - FICO System")


# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.on_event("startup")
def create_auth_tables():
    Base.metadata.create_all(bind=engine)

@app.get("/")
def home():
    return {
        "message": "STACKLY OneERP API is running",
        "status": "success",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/db-check")
def db_check():
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT DATABASE(), VERSION()")
        ).fetchone()

    return {
        "database": result[0],
        "mysql_version": result[1],
        "status": "connected",
    }


# Register all application routers
app.include_router(auth_router)

app.include_router(coa_router)
app.include_router(ar_customers_router)
app.include_router(ar_invoices_router)
app.include_router(ar_payments_router)
app.include_router(ar_workflow_router)
app.include_router(financial_reports_router)
app.include_router(company_setup_router)
app.include_router(ap_router)
app.include_router(fiscal_calendar_router)
app.include_router(journal_entry_router)
app.include_router(general_ledger_router)
app.include_router(fixed_assets_router)
app.include_router(budgeting_router)
app.include_router(gst_router)
app.include_router(dashboard_router)
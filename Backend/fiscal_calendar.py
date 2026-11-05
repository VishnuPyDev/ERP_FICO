"""Fiscal year and period API for STACKLY OneERP.
Register in main.py:
from fiscal_calendar import router as fiscal_calendar_router
app.include_router(fiscal_calendar_router)

This module uses the existing database.py (get_db, engine). To avoid silently
colliding with any legacy fiscal tables, it creates fico_fiscal_years and
fico_fiscal_periods. Company table is expected to be `companies(id)`.
"""
from datetime import date
from calendar import monthrange
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
try:
    from database import get_db, engine
except ImportError:
    from .database import get_db, engine

router = APIRouter(prefix="/api/fiscal", tags=["Fiscal Year and Period"])

def ensure_tables():
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_fiscal_years (
          id INT AUTO_INCREMENT PRIMARY KEY, company_id INT NOT NULL,
          year_code VARCHAR(30) NOT NULL, year_name VARCHAR(100) NOT NULL,
          start_date DATE NOT NULL, end_date DATE NOT NULL,
          status VARCHAR(20) NOT NULL DEFAULT 'Draft', is_current BOOLEAN NOT NULL DEFAULT FALSE,
          notes VARCHAR(500), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_fy_company_code(company_id, year_code),
          INDEX ix_fy_company_dates(company_id,start_date,end_date)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_fiscal_periods (
          id INT AUTO_INCREMENT PRIMARY KEY, fiscal_year_id INT NOT NULL, company_id INT NOT NULL,
          period_number INT NOT NULL, period_name VARCHAR(100) NOT NULL,
          start_date DATE NOT NULL, end_date DATE NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'Open',
          notes VARCHAR(500), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_period_year_number(fiscal_year_id,period_number),
          INDEX ix_period_company_dates(company_id,start_date,end_date),
          CONSTRAINT fk_fico_period_year FOREIGN KEY(fiscal_year_id)
            REFERENCES fico_fiscal_years(id) ON DELETE CASCADE
        ) ENGINE=InnoDB"""))

@router.on_event("startup")
def startup():
    ensure_tables()

def company_ok(db, company_id):
    try:
        return db.execute(text("SELECT id FROM company WHERE id=:id LIMIT 1"), {"id": company_id}).first() is not None
    except SQLAlchemyError as exc:
        raise HTTPException(500, "Cannot query `company`. Confirm the Company table name in models.py.") from exc

def fetch_one(db, table, record_id):
    row = db.execute(text(f"SELECT * FROM {table} WHERE id=:id"), {"id": record_id}).mappings().first()
    if not row:
        raise HTTPException(404, f"{'Fiscal year' if table == 'fico_fiscal_years' else 'Fiscal period'} not found")
    return dict(row)

def check_dates(start, end):
    if end < start:
        raise HTTPException(422, "End date must be on or after start date")

class YearCreate(BaseModel):
    company_id: int = Field(gt=0)
    year_code: str = Field(min_length=2, max_length=30)
    year_name: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    status: str = "Draft"
    is_current: bool = False
    notes: Optional[str] = Field(None, max_length=500)
    @field_validator("year_code", "year_name")
    @classmethod
    def clean_text(cls, v):
        v = v.strip()
        if not v: raise ValueError("Cannot be empty")
        return v
    @field_validator("status")
    @classmethod
    def clean_status(cls, v):
        v = v.strip().title()
        if v not in {"Draft","Open","Closed"}: raise ValueError("Status must be Draft, Open, or Closed")
        return v
    @model_validator(mode="after")
    def date_order(self):
        if self.end_date < self.start_date: raise ValueError("End date must be on or after start date")
        return self

class YearUpdate(BaseModel):
    year_code: Optional[str] = Field(None, min_length=2, max_length=30)
    year_name: Optional[str] = Field(None, min_length=2, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None
    is_current: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=500)
    @field_validator("status")
    @classmethod
    def clean_status(cls, v):
        if v is None: return v
        v = v.strip().title()
        if v not in {"Draft","Open","Closed"}: raise ValueError("Status must be Draft, Open, or Closed")
        return v

class PeriodCreate(BaseModel):
    company_id: int = Field(gt=0)
    fiscal_year_id: int = Field(gt=0)
    period_number: int = Field(ge=1, le=13)
    period_name: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    status: str = "Open"
    notes: Optional[str] = Field(None, max_length=500)
    @field_validator("period_name")
    @classmethod
    def clean_name(cls, v):
        v = v.strip()
        if not v: raise ValueError("Cannot be empty")
        return v
    @field_validator("status")
    @classmethod
    def clean_status(cls, v):
        v = v.strip().title()
        if v not in {"Open","Closed","Locked"}: raise ValueError("Status must be Open, Closed, or Locked")
        return v
    @model_validator(mode="after")
    def date_order(self):
        if self.end_date < self.start_date: raise ValueError("End date must be on or after start date")
        return self

class PeriodUpdate(BaseModel):
    period_number: Optional[int] = Field(None, ge=1, le=13)
    period_name: Optional[str] = Field(None, min_length=2, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=500)
    @field_validator("status")
    @classmethod
    def clean_status(cls, v):
        if v is None: return v
        v = v.strip().title()
        if v not in {"Open","Closed","Locked"}: raise ValueError("Status must be Open, Closed, or Locked")
        return v

@router.get("/health")
def health():
    return {"module":"Fiscal Year and Period","status":"ready"}

@router.get("/years")
def list_years(company_id: int = Query(gt=0), status_filter: Optional[str] = Query(None, alias="status"),
               search: Optional[str] = None, db: Session = Depends(get_db)):
    if not company_ok(db, company_id): raise HTTPException(404, "Company not found")
    sql = "SELECT * FROM fico_fiscal_years WHERE company_id=:company_id"
    args = {"company_id": company_id}
    if status_filter: sql += " AND status=:status"; args["status"] = status_filter.title()
    if search: sql += " AND (year_code LIKE :search OR year_name LIKE :search)"; args["search"] = f"%{search.strip()}%"
    sql += " ORDER BY start_date DESC, id DESC"
    return [dict(r) for r in db.execute(text(sql), args).mappings().all()]

@router.post("/years", status_code=201)
def create_year(x: YearCreate, db: Session = Depends(get_db)):
    if not company_ok(db, x.company_id): raise HTTPException(404, "Company not found")
    args = x.model_dump()
    overlap = db.execute(text("""SELECT id FROM fico_fiscal_years WHERE company_id=:company_id
      AND start_date<=:end_date AND end_date>=:start_date LIMIT 1"""), args).first()
    if overlap: raise HTTPException(409, "Fiscal year dates overlap an existing year")
    try:
        if x.is_current: db.execute(text("UPDATE fico_fiscal_years SET is_current=0 WHERE company_id=:company_id"), {"company_id":x.company_id})
        result = db.execute(text("""INSERT INTO fico_fiscal_years
          (company_id,year_code,year_name,start_date,end_date,status,is_current,notes)
          VALUES (:company_id,:year_code,:year_name,:start_date,:end_date,:status,:is_current,:notes)"""), args)
        db.commit()
        return fetch_one(db,"fico_fiscal_years",result.lastrowid)
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,"Year code already exists for this company") from exc
    except SQLAlchemyError as exc:
        db.rollback(); raise HTTPException(500,"Could not save fiscal year") from exc

@router.get("/years/{year_id}")
def get_year(year_id: int, db: Session = Depends(get_db)): return fetch_one(db,"fico_fiscal_years",year_id)

@router.put("/years/{year_id}")
def update_year(year_id: int, x: YearUpdate, db: Session = Depends(get_db)):
    old = fetch_one(db,"fico_fiscal_years",year_id); changes = x.model_dump(exclude_unset=True)
    if not changes: return old
    check_dates(changes.get("start_date",old["start_date"]),changes.get("end_date",old["end_date"]))
    if changes.get("is_current"):
        db.execute(text("UPDATE fico_fiscal_years SET is_current=0 WHERE company_id=:c"),{"c":old["company_id"]})
    sql = ", ".join(f"{k}=:{k}" for k in changes); changes["id"] = year_id
    try:
        db.execute(text(f"UPDATE fico_fiscal_years SET {sql} WHERE id=:id"),changes); db.commit()
        return fetch_one(db,"fico_fiscal_years",year_id)
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,"Year code already exists") from exc
    except SQLAlchemyError as exc:
        db.rollback(); raise HTTPException(500,"Could not update fiscal year") from exc

@router.post("/years/{year_id}/generate-periods", status_code=201)
def generate_periods(year_id: int, db: Session = Depends(get_db)):
    y = fetch_one(db,"fico_fiscal_years",year_id)
    if y["status"] == "Closed": raise HTTPException(409,"Cannot generate periods for a closed year")
    cursor, number, made = y["start_date"], 1, []
    while cursor <= y["end_date"] and number <= 13:
        last = monthrange(cursor.year,cursor.month)[1]
        end = min(date(cursor.year,cursor.month,last),y["end_date"])
        exists = db.execute(text("SELECT id FROM fico_fiscal_periods WHERE fiscal_year_id=:y AND period_number=:n"),
                            {"y":year_id,"n":number}).first()
        if not exists:
            result = db.execute(text("""INSERT INTO fico_fiscal_periods
              (fiscal_year_id,company_id,period_number,period_name,start_date,end_date,status)
              VALUES (:y,:c,:n,:name,:s,:e,'Open')"""),
              {"y":year_id,"c":y["company_id"],"n":number,"name":cursor.strftime("%B %Y"),"s":cursor,"e":end})
            made.append(result.lastrowid)
        cursor = date(end.year + (end.month // 12),(end.month % 12)+1,1); number += 1
    db.commit()
    return {"fiscal_year_id":year_id,"created_count":len(made),"created_period_ids":made}

@router.get("/periods")
def list_periods(company_id: int = Query(gt=0), fiscal_year_id: Optional[int] = Query(None,gt=0),
                 status_filter: Optional[str] = Query(None,alias="status"), search: Optional[str] = None,
                 db: Session = Depends(get_db)):
    if not company_ok(db,company_id): raise HTTPException(404,"Company not found")
    sql = "SELECT * FROM fico_fiscal_periods WHERE company_id=:company_id"; args={"company_id":company_id}
    if fiscal_year_id: sql += " AND fiscal_year_id=:fy"; args["fy"]=fiscal_year_id
    if status_filter: sql += " AND status=:status"; args["status"]=status_filter.title()
    if search: sql += " AND period_name LIKE :search"; args["search"]=f"%{search.strip()}%"
    sql += " ORDER BY start_date,period_number"
    return [dict(r) for r in db.execute(text(sql),args).mappings().all()]

@router.post("/periods", status_code=201)
def create_period(x: PeriodCreate, db: Session = Depends(get_db)):
    if not company_ok(db,x.company_id): raise HTTPException(404,"Company not found")
    y = fetch_one(db,"fico_fiscal_years",x.fiscal_year_id)
    if y["company_id"] != x.company_id: raise HTTPException(400,"Fiscal year does not belong to this company")
    if x.start_date < y["start_date"] or x.end_date > y["end_date"]: raise HTTPException(400,"Period dates must be within the fiscal year")
    overlap = db.execute(text("""SELECT id FROM fico_fiscal_periods WHERE fiscal_year_id=:y
      AND start_date<=:e AND end_date>=:s LIMIT 1"""),{"y":x.fiscal_year_id,"s":x.start_date,"e":x.end_date}).first()
    if overlap: raise HTTPException(409,"Period dates overlap an existing period")
    try:
        r=db.execute(text("""INSERT INTO fico_fiscal_periods
          (fiscal_year_id,company_id,period_number,period_name,start_date,end_date,status,notes)
          VALUES (:fiscal_year_id,:company_id,:period_number,:period_name,:start_date,:end_date,:status,:notes)"""),x.model_dump())
        db.commit(); return fetch_one(db,"fico_fiscal_periods",r.lastrowid)
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,"Period number already exists in this fiscal year") from exc
    except SQLAlchemyError as exc:
        db.rollback(); raise HTTPException(500,"Could not save fiscal period") from exc

@router.get("/periods/{period_id}")
def get_period(period_id:int,db:Session=Depends(get_db)): return fetch_one(db,"fico_fiscal_periods",period_id)

@router.put("/periods/{period_id}")
def update_period(period_id:int,x:PeriodUpdate,db:Session=Depends(get_db)):
    old=fetch_one(db,"fico_fiscal_periods",period_id); changes=x.model_dump(exclude_unset=True)
    if not changes:return old
    start=changes.get("start_date",old["start_date"]); end=changes.get("end_date",old["end_date"]); check_dates(start,end)
    y=fetch_one(db,"fico_fiscal_years",old["fiscal_year_id"])
    if start<y["start_date"] or end>y["end_date"]: raise HTTPException(400,"Period dates must be within the fiscal year")
    sql=", ".join(f"{k}=:{k}" for k in changes); changes["id"]=period_id
    try:
        db.execute(text(f"UPDATE fico_fiscal_periods SET {sql} WHERE id=:id"),changes); db.commit()
        return fetch_one(db,"fico_fiscal_periods",period_id)
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409,"Period number already exists in this fiscal year") from exc
    except SQLAlchemyError as exc:
        db.rollback(); raise HTTPException(500,"Could not update fiscal period") from exc

@router.patch("/periods/{period_id}/status")
def set_period_status(period_id:int,new_status:str=Query(),db:Session=Depends(get_db)):
    s=new_status.strip().title()
    if s not in {"Open","Closed","Locked"}: raise HTTPException(422,"Status must be Open, Closed, or Locked")
    p=fetch_one(db,"fico_fiscal_periods",period_id); y=fetch_one(db,"fico_fiscal_years",p["fiscal_year_id"])
    if y["status"]=="Closed" and s=="Open": raise HTTPException(409,"Cannot open a period in a closed fiscal year")
    db.execute(text("UPDATE fico_fiscal_periods SET status=:s WHERE id=:id"),{"s":s,"id":period_id}); db.commit()
    return fetch_one(db,"fico_fiscal_periods",period_id)

@router.delete("/periods/{period_id}",status_code=204)
def delete_period(period_id:int,db:Session=Depends(get_db)):
    p=fetch_one(db,"fico_fiscal_periods",period_id)
    if p["status"]!="Open": raise HTTPException(409,"Only Open periods can be deleted")
    db.execute(text("DELETE FROM fico_fiscal_periods WHERE id=:id"),{"id":period_id}); db.commit()

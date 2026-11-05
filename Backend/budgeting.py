"""STACKLY OneERP FICO Budgeting API. Uses existing database.py and ERP master tables."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
try:
    from database import get_db, engine
except ImportError:
    from .database import get_db, engine

router = APIRouter(prefix="/api/budgeting", tags=["Budgeting"])
CENT = Decimal("0.01")
STATUSES = {"DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "ARCHIVED"}

def money(v):
    try: x = Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError): raise HTTPException(422, "Invalid amount")
    if not x.is_finite() or x < 0 or x > Decimal("9999999999999.99"):
        raise HTTPException(422, "Amount must be between 0 and 9999999999999.99")
    return x

def ensure_tables():
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_budgets(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, company_id INT NOT NULL,
          fiscal_year_id INT NOT NULL, budget_code VARCHAR(40) NOT NULL,
          budget_name VARCHAR(150) NOT NULL, description VARCHAR(500),
          version_no INT NOT NULL DEFAULT 1, scenario VARCHAR(30) NOT NULL DEFAULT 'BASE',
          status VARCHAR(20) NOT NULL DEFAULT 'DRAFT', owner_name VARCHAR(100),
          submitted_by VARCHAR(100), approved_by VARCHAR(100), approved_at DATETIME,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_budget_version(company_id,budget_code,version_no),
          INDEX ix_budget_company_year(company_id,fiscal_year_id,status)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_budget_lines(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, budget_id BIGINT NOT NULL,
          account_id INT NOT NULL, cost_center VARCHAR(60), line_description VARCHAR(255),
          annual_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
          UNIQUE KEY uq_budget_line(budget_id,account_id,cost_center),
          CONSTRAINT fk_budget_line FOREIGN KEY(budget_id) REFERENCES fico_budgets(id) ON DELETE CASCADE
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_budget_monthly_amounts(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, budget_line_id BIGINT NOT NULL,
          fiscal_period_id INT NOT NULL, budget_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
          UNIQUE KEY uq_budget_month(budget_line_id,fiscal_period_id),
          CONSTRAINT fk_budget_month_line FOREIGN KEY(budget_line_id) REFERENCES fico_budget_lines(id) ON DELETE CASCADE
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_budget_approvals(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, budget_id BIGINT NOT NULL,
          action VARCHAR(20) NOT NULL, action_by VARCHAR(100), comments VARCHAR(500),
          action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          CONSTRAINT fk_budget_approval FOREIGN KEY(budget_id) REFERENCES fico_budgets(id) ON DELETE CASCADE
        ) ENGINE=InnoDB"""))

@router.on_event("startup")
def startup(): ensure_tables()

def company(db, cid):
    r=db.execute(text("SELECT id,company_code FROM company WHERE id=:id"),{"id":cid}).mappings().first()
    if not r: raise HTTPException(404,"Company not found")
    return dict(r)

def year(db,cid,yid):
    r=db.execute(text("""SELECT id,company_id,year_code,year_name,start_date,end_date,status
                         FROM fico_fiscal_years WHERE id=:y AND company_id=:c"""),
                 {"y":yid,"c":cid}).mappings().first()
    if not r: raise HTTPException(404,"Fiscal year not found for company")
    return dict(r)

def budget(db,bid):
    r=db.execute(text("SELECT * FROM fico_budgets WHERE id=:id"),{"id":bid}).mappings().first()
    if not r: raise HTTPException(404,"Budget not found")
    return dict(r)

def periods(db,cid,yid):
    return [dict(x) for x in db.execute(text("""SELECT id,period_number,period_name,start_date,end_date,status
      FROM fico_fiscal_periods WHERE company_id=:c AND fiscal_year_id=:y ORDER BY period_number"""),
      {"c":cid,"y":yid}).mappings().all()]

class BudgetIn(BaseModel):
    company_id:int=Field(gt=0)
    fiscal_year_id:int=Field(gt=0)
    budget_code:str=Field(min_length=2,max_length=40)
    budget_name:str=Field(min_length=2,max_length=150)
    description:Optional[str]=Field(None,max_length=500)
    scenario:str=Field(default="BASE",max_length=30)
    owner_name:Optional[str]=Field(None,max_length=100)
    @field_validator("budget_code","budget_name","scenario")
    @classmethod
    def strip(cls,v): return v.strip()

class BudgetPatch(BaseModel):
    budget_name:Optional[str]=Field(None,min_length=2,max_length=150)
    description:Optional[str]=Field(None,max_length=500)
    scenario:Optional[str]=Field(None,max_length=30)
    owner_name:Optional[str]=Field(None,max_length=100)

class LineIn(BaseModel):
    account_id:int=Field(gt=0)
    cost_center:Optional[str]=Field(None,max_length=60)
    line_description:Optional[str]=Field(None,max_length=255)
    annual_amount:Decimal=Field(ge=0,max_digits=15,decimal_places=2)
    monthly_amounts:List[Decimal]=Field(min_length=1,max_length=24)

class LinesIn(BaseModel):
    lines:List[LineIn]=Field(min_length=1,max_length=500)

class ActionIn(BaseModel):
    action_by:Optional[str]=Field(None,max_length=100)
    comments:Optional[str]=Field(None,max_length=500)

def editable(b):
    if b["status"]!="DRAFT": raise HTTPException(409,"Only DRAFT budgets can be edited")

@router.get("/health")
def health(): return {"module":"Budgeting","status":"ready"}

@router.get("/context")
def get_context(company_id:int=Query(gt=0),fiscal_year_id:Optional[int]=Query(None,gt=0),db:Session=Depends(get_db)):
    co=company(db,company_id)
    ys=db.execute(text("""SELECT id,year_code,year_name,start_date,end_date,status
      FROM fico_fiscal_years WHERE company_id=:c ORDER BY start_date DESC"""),{"c":company_id}).mappings().all()
    ac=db.execute(text("""SELECT id,account_code,account_name,account_type,normal_balance
      FROM chart_of_accounts WHERE company_code=:code AND is_active=TRUE ORDER BY account_code"""),
      {"code":co["company_code"]}).mappings().all()
    return {"company":co,"fiscal_years":[dict(x) for x in ys],"accounts":[dict(x) for x in ac],
            "periods":periods(db,company_id,fiscal_year_id) if fiscal_year_id else []}

@router.get("/budgets")
def list_budgets(company_id:int=Query(gt=0),fiscal_year_id:Optional[int]=Query(None,gt=0),
                 status_filter:Optional[str]=Query(None,alias="status"),search:Optional[str]=None,
                 db:Session=Depends(get_db)):
    company(db,company_id)
    q="""SELECT b.*,y.year_code,y.year_name FROM fico_budgets b
         JOIN fico_fiscal_years y ON y.id=b.fiscal_year_id WHERE b.company_id=:c"""
    p={"c":company_id}
    if fiscal_year_id: q+=" AND b.fiscal_year_id=:y";p["y"]=fiscal_year_id
    if status_filter:
        s=status_filter.upper()
        if s not in STATUSES: raise HTTPException(422,"Invalid status")
        q+=" AND b.status=:s";p["s"]=s
    if search and search.strip(): q+=" AND (b.budget_code LIKE :term OR b.budget_name LIKE :term)";p["term"]="%"+search.strip()+"%"
    q+=" ORDER BY b.id DESC"
    return [dict(x) for x in db.execute(text(q),p).mappings().all()]

@router.post("/budgets",status_code=201)
def create_budget(x:BudgetIn,db:Session=Depends(get_db)):
    company(db,x.company_id);year(db,x.company_id,x.fiscal_year_id)
    try:
        r=db.execute(text("""INSERT INTO fico_budgets(company_id,fiscal_year_id,budget_code,budget_name,
          description,scenario,owner_name) VALUES(:c,:y,:code,:name,:d,:s,:o)"""),
          {"c":x.company_id,"y":x.fiscal_year_id,"code":x.budget_code.upper(),"name":x.budget_name,
           "d":x.description,"s":x.scenario.upper(),"o":x.owner_name})
        db.commit()
        return {"message":"Budget created","budget_id":r.lastrowid,"status":"DRAFT"}
    except IntegrityError as e:
        db.rollback();raise HTTPException(409,"Budget code/version already exists") from e

@router.get("/budgets/{bid}")
def get_budget(bid:int,db:Session=Depends(get_db)):
    b=budget(db,bid)
    ls=db.execute(text("""SELECT l.*,a.account_code,a.account_name,a.account_type
      FROM fico_budget_lines l JOIN chart_of_accounts a ON a.id=l.account_id
      WHERE l.budget_id=:b ORDER BY a.account_code"""),{"b":bid}).mappings().all()
    out=[]
    for raw in ls:
        l=dict(raw)
        l["monthly_amounts"]=[dict(x) for x in db.execute(text("""SELECT m.fiscal_period_id,p.period_number,
          p.period_name,m.budget_amount FROM fico_budget_monthly_amounts m
          JOIN fico_fiscal_periods p ON p.id=m.fiscal_period_id
          WHERE m.budget_line_id=:id ORDER BY p.period_number"""),{"id":l["id"]}).mappings().all()]
        l["annual_amount"]=float(l["annual_amount"]);out.append(l)
    b["lines"]=out
    b["approvals"]=[dict(x) for x in db.execute(text("SELECT * FROM fico_budget_approvals WHERE budget_id=:b ORDER BY id"),{"b":bid}).mappings().all()]
    return b

@router.put("/budgets/{bid}")
def update_budget(bid:int,x:BudgetPatch,db:Session=Depends(get_db)):
    editable(budget(db,bid));v=x.model_dump(exclude_unset=True)
    if not v: raise HTTPException(422,"No fields supplied")
    sets=[];p={"id":bid}
    for k,val in v.items(): sets.append(k+"=:"+k);p[k]=val.strip() if isinstance(val,str) else val
    db.execute(text("UPDATE fico_budgets SET "+",".join(sets)+" WHERE id=:id"),p);db.commit()
    return {"message":"Budget updated"}

@router.put("/budgets/{bid}/lines")
def save_lines(bid:int,x:LinesIn,db:Session=Depends(get_db)):
    b=budget(db,bid);editable(b);co=company(db,b["company_id"]);ps=periods(db,b["company_id"],b["fiscal_year_id"])
    if not ps: raise HTTPException(422,"Generate fiscal periods before entering budget allocations")
    seen=set();validated=[]
    for line in x.lines:
        if line.account_id in seen: raise HTTPException(422,"Duplicate account line")
        seen.add(line.account_id)
        ok=db.execute(text("""SELECT id FROM chart_of_accounts WHERE id=:a AND company_code=:code AND is_active=TRUE"""),
                      {"a":line.account_id,"code":co["company_code"]}).first()
        if not ok: raise HTTPException(422,f"Account {line.account_id} is not active for this company")
        ms=[money(v) for v in line.monthly_amounts];annual=money(line.annual_amount)
        if len(ms)!=len(ps): raise HTTPException(422,f"Enter exactly {len(ps)} period allocations")
        if sum(ms,Decimal("0.00"))!=annual: raise HTTPException(422,"Monthly allocations must equal annual amount")
        validated.append((line,ms,annual))
    try:
        db.execute(text("DELETE FROM fico_budget_lines WHERE budget_id=:b"),{"b":bid})
        for line,ms,annual in validated:
            r=db.execute(text("""INSERT INTO fico_budget_lines(budget_id,account_id,cost_center,line_description,annual_amount)
              VALUES(:b,:a,:cc,:d,:annual)"""),{"b":bid,"a":line.account_id,"cc":line.cost_center,"d":line.line_description,"annual":annual})
            for p,m in zip(ps,ms):
                db.execute(text("""INSERT INTO fico_budget_monthly_amounts(budget_line_id,fiscal_period_id,budget_amount)
                  VALUES(:l,:p,:m)"""),{"l":r.lastrowid,"p":p["id"],"m":m})
        db.commit()
    except Exception: db.rollback();raise
    return {"message":"Budget lines saved","line_count":len(validated),"period_count":len(ps)}

@router.post("/budgets/{bid}/submit")
def submit(bid:int,x:ActionIn,db:Session=Depends(get_db)):
    b=budget(db,bid);editable(b)
    if not db.execute(text("SELECT COUNT(*) FROM fico_budget_lines WHERE budget_id=:b"),{"b":bid}).scalar():
        raise HTTPException(422,"Add budget lines before submitting")
    db.execute(text("UPDATE fico_budgets SET status='SUBMITTED',submitted_by=:u WHERE id=:b"),{"u":x.action_by,"b":bid})
    db.execute(text("INSERT INTO fico_budget_approvals(budget_id,action,action_by,comments) VALUES(:b,'SUBMITTED',:u,:c)"),
               {"b":bid,"u":x.action_by,"c":x.comments});db.commit()
    return {"message":"Submitted for approval","status":"SUBMITTED"}

@router.post("/budgets/{bid}/approve")
def approve(bid:int,x:ActionIn,db:Session=Depends(get_db)):
    b=budget(db,bid)
    if b["status"]!="SUBMITTED": raise HTTPException(409,"Only SUBMITTED budgets can be approved")
    db.execute(text("UPDATE fico_budgets SET status='APPROVED',approved_by=:u,approved_at=NOW() WHERE id=:b"),{"u":x.action_by,"b":bid})
    db.execute(text("INSERT INTO fico_budget_approvals(budget_id,action,action_by,comments) VALUES(:b,'APPROVED',:u,:c)"),
               {"b":bid,"u":x.action_by,"c":x.comments});db.commit()
    return {"message":"Budget approved","status":"APPROVED"}

@router.post("/budgets/{bid}/reject")
def reject(bid:int,x:ActionIn,db:Session=Depends(get_db)):
    b=budget(db,bid)
    if b["status"]!="SUBMITTED": raise HTTPException(409,"Only SUBMITTED budgets can be rejected")
    db.execute(text("UPDATE fico_budgets SET status='REJECTED' WHERE id=:b"),{"b":bid})
    db.execute(text("INSERT INTO fico_budget_approvals(budget_id,action,action_by,comments) VALUES(:b,'REJECTED',:u,:c)"),
               {"b":bid,"u":x.action_by,"c":x.comments});db.commit()
    return {"message":"Budget rejected","status":"REJECTED"}

@router.post("/budgets/{bid}/new-version",status_code=201)
def new_version(bid:int,db:Session=Depends(get_db)):
    old=budget(db,bid)
    if old["status"] not in {"APPROVED","REJECTED","ARCHIVED"}: raise HTTPException(409,"New version requires approved/rejected/archived source")
    v=db.execute(text("SELECT COALESCE(MAX(version_no),0)+1 FROM fico_budgets WHERE company_id=:c AND budget_code=:code"),
                 {"c":old["company_id"],"code":old["budget_code"]}).scalar()
    r=db.execute(text("""INSERT INTO fico_budgets(company_id,fiscal_year_id,budget_code,budget_name,description,
      version_no,scenario,owner_name) VALUES(:c,:y,:code,:name,:d,:v,:s,:o)"""),
      {"c":old["company_id"],"y":old["fiscal_year_id"],"code":old["budget_code"],"name":old["budget_name"],
       "d":old["description"],"v":v,"s":old["scenario"],"o":old["owner_name"]})
    new_id=r.lastrowid
    oldlines=db.execute(text("SELECT * FROM fico_budget_lines WHERE budget_id=:b"),{"b":bid}).mappings().all()
    for l in oldlines:
        nr=db.execute(text("""INSERT INTO fico_budget_lines(budget_id,account_id,cost_center,line_description,annual_amount)
          VALUES(:b,:a,:cc,:d,:n)"""),{"b":new_id,"a":l["account_id"],"cc":l["cost_center"],"d":l["line_description"],"n":l["annual_amount"]})
        months=db.execute(text("SELECT fiscal_period_id,budget_amount FROM fico_budget_monthly_amounts WHERE budget_line_id=:l"),{"l":l["id"]}).mappings().all()
        for m in months: db.execute(text("""INSERT INTO fico_budget_monthly_amounts(budget_line_id,fiscal_period_id,budget_amount)
          VALUES(:l,:p,:a)"""),{"l":nr.lastrowid,"p":m["fiscal_period_id"],"a":m["budget_amount"]})
    db.commit();return {"budget_id":new_id,"version_no":v,"status":"DRAFT"}

@router.get("/budgets/{bid}/variance")
def variance(bid:int,db:Session=Depends(get_db)):
    b=budget(db,bid)
    rows=db.execute(text("""SELECT l.id,l.account_id,a.account_code,a.account_name,l.cost_center,l.annual_amount
      FROM fico_budget_lines l JOIN chart_of_accounts a ON a.id=l.account_id
      WHERE l.budget_id=:b ORDER BY a.account_code"""),{"b":bid}).mappings().all()
    out=[]
    for raw in rows:
        r=dict(raw)
        actual=db.execute(text("""SELECT COALESCE(SUM(CASE WHEN UPPER(a.account_type) IN ('ASSET','EXPENSE','EXPENSES')
          THEN jl.debit-jl.credit ELSE jl.credit-jl.debit END),0)
          FROM fico_journal_entry_lines jl JOIN fico_journal_entries je ON je.id=jl.journal_entry_id
          JOIN chart_of_accounts a ON a.id=jl.account_id
          WHERE je.company_id=:c AND je.fiscal_year_id=:y AND je.status='POSTED' AND jl.account_id=:a"""),
          {"c":b["company_id"],"y":b["fiscal_year_id"],"a":r["account_id"]}).scalar()
        bud=money(r["annual_amount"]);act=money(actual or 0);var=bud-act
        r.update(annual_amount=float(bud),actual_amount=float(act),variance=float(var),
                 variance_percent=float(var/bud*100) if bud else 0)
        out.append(r)
    return {"budget":b,"lines":out}

@router.get("/summary")
def summary(company_id:int=Query(gt=0),fiscal_year_id:Optional[int]=Query(None,gt=0),db:Session=Depends(get_db)):
    company(db,company_id)
    q="""SELECT COUNT(*) total_budgets,SUM(status='DRAFT') draft_budgets,
      SUM(status='SUBMITTED') submitted_budgets,SUM(status='APPROVED') approved_budgets
      FROM fico_budgets WHERE company_id=:c"""
    p={"c":company_id}
    if fiscal_year_id:q+=" AND fiscal_year_id=:y";p["y"]=fiscal_year_id
    r=dict(db.execute(text(q),p).mappings().one())
    r={k:int(v or 0) for k,v in r.items()}
    t=db.execute(text("""SELECT COALESCE(SUM(l.annual_amount),0) FROM fico_budget_lines l
      JOIN fico_budgets b ON b.id=l.budget_id WHERE b.company_id=:c AND b.status='APPROVED'
      AND (:y IS NULL OR b.fiscal_year_id=:y)"""),{"c":company_id,"y":fiscal_year_id}).scalar()
    r["approved_budget_total"]=float(money(t or 0));return r

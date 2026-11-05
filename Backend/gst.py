"""
STACKLY OneERP - Indian GST Management API
Uses existing database.py (get_db, engine), company(id, company_code),
chart_of_accounts, fico_fiscal_years, fico_fiscal_periods and journal tables.
Creates GST-specific tables only. Does not file returns with GSTN.
"""
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional, List, Literal
import re, csv, io
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import Session
try:
    from database import get_db, engine
except ImportError:
    from .database import get_db, engine

router = APIRouter(prefix="/api/gst", tags=["GST Management"])
CENT = Decimal("0.01")
GST_TYPES = {"B2B", "B2C", "EXPORT", "SEZ", "DEEMED_EXPORT"}
DOC_TYPES = {"INVOICE", "CREDIT_NOTE", "DEBIT_NOTE"}
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

def dec(v):
    try: x = Decimal(str(v if v is not None else 0)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError): raise HTTPException(422, "Invalid monetary amount")
    if not x.is_finite() or x < 0 or x > Decimal("9999999999999.99"): raise HTTPException(422, "Amount is outside supported range")
    return x

def state_code(value):
    s = str(value or "").strip()
    if not re.fullmatch(r"\d{2}", s): raise HTTPException(422, "State code must be the 2-digit GST state code")
    return s

def ensure_tables():
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_gst_registrations(
          id INT AUTO_INCREMENT PRIMARY KEY, company_id INT NOT NULL, gstin VARCHAR(15) NOT NULL,
          legal_name VARCHAR(180) NOT NULL, trade_name VARCHAR(180), state_code CHAR(2) NOT NULL,
          state_name VARCHAR(80), registration_type VARCHAR(30) NOT NULL DEFAULT 'REGULAR',
          effective_from DATE, is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_gst_gstin(gstin), INDEX ix_gst_reg_company(company_id)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_gst_tax_rates(
          id INT AUTO_INCREMENT PRIMARY KEY, company_id INT NOT NULL, tax_name VARCHAR(80) NOT NULL,
          hsn_sac VARCHAR(20), rate DECIMAL(7,3) NOT NULL, cess_rate DECIMAL(7,3) NOT NULL DEFAULT 0,
          supply_type VARCHAR(20) NOT NULL DEFAULT 'GOODS', effective_from DATE,
          effective_to DATE, is_active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          INDEX ix_gst_rate_company(company_id)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_gst_invoices(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, company_id INT NOT NULL, company_code VARCHAR(30) NOT NULL,
          registration_id INT, doc_type VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
          invoice_number VARCHAR(60) NOT NULL, invoice_date DATE NOT NULL, financial_year VARCHAR(12),
          party_name VARCHAR(180) NOT NULL, party_gstin VARCHAR(15), place_state_code CHAR(2) NOT NULL,
          supply_category VARCHAR(20) NOT NULL, reverse_charge BOOLEAN NOT NULL DEFAULT FALSE,
          is_itc_eligible BOOLEAN NOT NULL DEFAULT TRUE, status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
          taxable_value DECIMAL(15,2) NOT NULL DEFAULT 0, cgst DECIMAL(15,2) NOT NULL DEFAULT 0,
          sgst DECIMAL(15,2) NOT NULL DEFAULT 0, igst DECIMAL(15,2) NOT NULL DEFAULT 0,
          cess DECIMAL(15,2) NOT NULL DEFAULT 0, total_tax DECIMAL(15,2) NOT NULL DEFAULT 0,
          invoice_total DECIMAL(15,2) NOT NULL DEFAULT 0, notes VARCHAR(500),
          journal_entry_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uq_gst_invoice(company_id,direction,invoice_number),
          INDEX ix_gst_inv_date(company_id,invoice_date), INDEX ix_gst_inv_status(company_id,status)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_gst_invoice_lines(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, invoice_id BIGINT NOT NULL, line_number INT NOT NULL,
          description VARCHAR(220) NOT NULL, hsn_sac VARCHAR(20), quantity DECIMAL(15,3) NOT NULL DEFAULT 1,
          unit VARCHAR(20), unit_price DECIMAL(15,2) NOT NULL, discount DECIMAL(15,2) NOT NULL DEFAULT 0,
          taxable_value DECIMAL(15,2) NOT NULL, gst_rate DECIMAL(7,3) NOT NULL,
          cgst DECIMAL(15,2) NOT NULL DEFAULT 0, sgst DECIMAL(15,2) NOT NULL DEFAULT 0,
          igst DECIMAL(15,2) NOT NULL DEFAULT 0, cess DECIMAL(15,2) NOT NULL DEFAULT 0,
          line_total DECIMAL(15,2) NOT NULL,
          CONSTRAINT fk_gst_line_invoice FOREIGN KEY(invoice_id) REFERENCES fico_gst_invoices(id) ON DELETE CASCADE,
          INDEX ix_gst_line_invoice(invoice_id)
        ) ENGINE=InnoDB"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS fico_gst_return_periods(
          id BIGINT AUTO_INCREMENT PRIMARY KEY, company_id INT NOT NULL, financial_year VARCHAR(12) NOT NULL,
          tax_period CHAR(7) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
          filed_at DATETIME, acknowledgement_number VARCHAR(80), notes VARCHAR(500),
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uq_gst_return_period(company_id,tax_period)
        ) ENGINE=InnoDB"""))

@router.on_event("startup")
def startup(): ensure_tables()

def company(db, cid):
    row=db.execute(text("SELECT id, company_code FROM company WHERE id=:id"),{"id":cid}).mappings().first()
    if not row: raise HTTPException(404,"Company not found. Verify Company ID.")
    return dict(row)

class RegistrationIn(BaseModel):
    company_id:int=Field(gt=0); gstin:str=Field(min_length=15,max_length=15)
    legal_name:str=Field(min_length=2,max_length=180); trade_name:Optional[str]=None
    state_code:str; state_name:Optional[str]=None
    registration_type:str="REGULAR"; effective_from:Optional[date]=None
    @field_validator("gstin")
    @classmethod
    def valid_gstin(cls,v):
        v=v.strip().upper()
        if not GSTIN_RE.fullmatch(v): raise ValueError("Enter a valid 15-character GSTIN format")
        return v
    @field_validator("state_code")
    @classmethod
    def valid_state(cls,v): return state_code(v)

class RateIn(BaseModel):
    company_id:int=Field(gt=0); tax_name:str=Field(min_length=2,max_length=80)
    hsn_sac:Optional[str]=None; rate:Decimal=Field(ge=0,le=100)
    cess_rate:Decimal=Field(default=0,ge=0,le=100); supply_type:str="GOODS"
    effective_from:Optional[date]=None; effective_to:Optional[date]=None

class InvoiceLineIn(BaseModel):
    description:str=Field(min_length=2,max_length=220); hsn_sac:Optional[str]=None
    quantity:Decimal=Field(gt=0); unit:Optional[str]=None
    unit_price:Decimal=Field(ge=0); discount:Decimal=Field(default=0,ge=0)
    gst_rate:Decimal=Field(ge=0,le=100); cess_rate:Decimal=Field(default=0,ge=0,le=100)

class InvoiceIn(BaseModel):
    company_id:int=Field(gt=0); registration_id:Optional[int]=None
    direction:Literal["SALES","PURCHASE"]; doc_type:Literal["INVOICE","CREDIT_NOTE","DEBIT_NOTE"]="INVOICE"
    invoice_number:str=Field(min_length=1,max_length=60); invoice_date:date
    financial_year:Optional[str]=None; party_name:str=Field(min_length=2,max_length=180)
    party_gstin:Optional[str]=None; place_state_code:str
    supply_category:Literal["B2B","B2C","EXPORT","SEZ","DEEMED_EXPORT"]="B2B"
    reverse_charge:bool=False; is_itc_eligible:bool=True; notes:Optional[str]=None
    lines:List[InvoiceLineIn]=Field(min_length=1,max_length=200)
    @field_validator("place_state_code")
    @classmethod
    def valid_state(cls,v): return state_code(v)
    @field_validator("party_gstin")
    @classmethod
    def valid_party_gstin(cls,v):
        if v:
            v=v.strip().upper()
            if not GSTIN_RE.fullmatch(v): raise ValueError("Invalid party GSTIN format")
        return v
    @model_validator(mode="after")
    def note_direction(self):
        if self.doc_type in {"CREDIT_NOTE","DEBIT_NOTE"} and not self.invoice_number.strip():
            raise ValueError("Document number is required")
        return self

def invoice_detail(db, iid):
    h=db.execute(text("SELECT * FROM fico_gst_invoices WHERE id=:id"),{"id":iid}).mappings().first()
    if not h: raise HTTPException(404,"GST document not found")
    out=dict(h)
    out["lines"]=[dict(x) for x in db.execute(text("SELECT * FROM fico_gst_invoice_lines WHERE invoice_id=:id ORDER BY line_number"),{"id":iid}).mappings().all()]
    return out

@router.get("/health")
def health(): return {"module":"GST Management","status":"ready","filing":"GSTN portal filing is not enabled"}

@router.get("/context")
def context(company_id:int=Query(gt=0),db:Session=Depends(get_db)):
    co=company(db,company_id)
    accounts=db.execute(text("""SELECT id,account_code,account_name,account_type,is_active
      FROM chart_of_accounts WHERE company_code=:code AND is_active=TRUE ORDER BY account_code"""),{"code":co["company_code"]}).mappings().all()
    years=db.execute(text("SELECT id,year_code,year_name,start_date,end_date,status FROM fico_fiscal_years WHERE company_id=:cid ORDER BY start_date DESC"),{"cid":company_id}).mappings().all()
    regs=db.execute(text("SELECT * FROM fico_gst_registrations WHERE company_id=:cid AND is_active=TRUE ORDER BY id"),{"cid":company_id}).mappings().all()
    rates=db.execute(text("SELECT * FROM fico_gst_tax_rates WHERE company_id=:cid AND is_active=TRUE ORDER BY rate"),{"cid":company_id}).mappings().all()
    return {"company":co,"accounts":[dict(x) for x in accounts],"fiscal_years":[dict(x) for x in years],
            "registrations":[dict(x) for x in regs],"tax_rates":[dict(x) for x in rates]}

@router.post("/registrations",status_code=201)
def create_registration(p:RegistrationIn,db:Session=Depends(get_db)):
    company(db,p.company_id)
    try:
        r=db.execute(text("""INSERT INTO fico_gst_registrations(company_id,gstin,legal_name,trade_name,state_code,state_name,registration_type,effective_from)
        VALUES(:company_id,:gstin,:legal_name,:trade_name,:state_code,:state_name,:registration_type,:effective_from)"""),p.model_dump())
        db.commit()
        return dict(db.execute(text("SELECT * FROM fico_gst_registrations WHERE id=:id"),{"id":r.lastrowid}).mappings().one())
    except IntegrityError as e: db.rollback(); raise HTTPException(409,"GSTIN already registered") from e

@router.get("/registrations")
def list_registrations(company_id:int=Query(gt=0),db:Session=Depends(get_db)):
    company(db,company_id)
    return [dict(x) for x in db.execute(text("SELECT * FROM fico_gst_registrations WHERE company_id=:cid ORDER BY id DESC"),{"cid":company_id}).mappings().all()]

@router.post("/tax-rates",status_code=201)
def create_rate(p:RateIn,db:Session=Depends(get_db)):
    company(db,p.company_id)
    if p.effective_from and p.effective_to and p.effective_to<p.effective_from: raise HTTPException(422,"Effective-to date cannot precede effective-from date")
    r=db.execute(text("""INSERT INTO fico_gst_tax_rates(company_id,tax_name,hsn_sac,rate,cess_rate,supply_type,effective_from,effective_to)
    VALUES(:company_id,:tax_name,:hsn_sac,:rate,:cess_rate,:supply_type,:effective_from,:effective_to)"""),p.model_dump())
    db.commit()
    return {"id":r.lastrowid,"message":"GST rate created"}

@router.get("/tax-rates")
def list_rates(company_id:int=Query(gt=0),db:Session=Depends(get_db)):
    company(db,company_id)
    return [dict(x) for x in db.execute(text("SELECT * FROM fico_gst_tax_rates WHERE company_id=:cid ORDER BY rate"),{"cid":company_id}).mappings().all()]

@router.post("/invoices",status_code=201)
def create_invoice(p:InvoiceIn,db:Session=Depends(get_db)):
    co=company(db,p.company_id)
    if p.registration_id:
        reg=db.execute(text("SELECT id,state_code FROM fico_gst_registrations WHERE id=:id AND company_id=:cid AND is_active=TRUE"),
          {"id":p.registration_id,"cid":p.company_id}).mappings().first()
        if not reg: raise HTTPException(422,"Selected GST registration is not active or does not belong to this company")
        origin=reg["state_code"]
    else:
        regs=db.execute(text("SELECT state_code FROM fico_gst_registrations WHERE company_id=:cid AND is_active=TRUE ORDER BY id LIMIT 1"),{"cid":p.company_id}).mappings().first()
        origin=regs["state_code"] if regs else None
    interstate = bool(origin and origin != p.place_state_code) or p.supply_category in {"EXPORT","SEZ"}
    sums={k:Decimal("0.00") for k in ("taxable","cgst","sgst","igst","cess","tax")}
    prepared=[]
    for n,line in enumerate(p.lines,1):
        base=dec(line.quantity*line.unit_price-line.discount)
        if line.discount>line.quantity*line.unit_price: raise HTTPException(422,f"Line {n}: discount cannot exceed gross value")
        tax=dec(base*line.gst_rate/Decimal(100))
        cess=dec(base*line.cess_rate/Decimal(100))
        cg=Decimal("0.00") if interstate else dec(tax/2)
        sg=Decimal("0.00") if interstate else dec(tax-cg)
        ig=tax if interstate else Decimal("0.00")
        total=dec(base+tax+cess)
        prepared.append({"line_number":n,"description":line.description,"hsn_sac":line.hsn_sac,"quantity":line.quantity,"unit":line.unit,
          "unit_price":dec(line.unit_price),"discount":dec(line.discount),"taxable_value":base,"gst_rate":line.gst_rate,
          "cgst":cg,"sgst":sg,"igst":ig,"cess":cess,"line_total":total})
        sums["taxable"]+=base; sums["cgst"]+=cg; sums["sgst"]+=sg; sums["igst"]+=ig; sums["cess"]+=cess; sums["tax"]+=tax+cess
    total=dec(sums["taxable"]+sums["cgst"]+sums["sgst"]+sums["igst"]+sums["cess"])
    vals={"company_id":p.company_id,"company_code":co["company_code"],"registration_id":p.registration_id,
      "doc_type":p.doc_type,"direction":p.direction,"invoice_number":p.invoice_number.strip(),
      "invoice_date":p.invoice_date,"financial_year":p.financial_year,"party_name":p.party_name,
      "party_gstin":p.party_gstin,"place_state_code":p.place_state_code,"supply_category":p.supply_category,
      "reverse_charge":p.reverse_charge,"is_itc_eligible":p.is_itc_eligible,"taxable_value":dec(sums["taxable"]),
      "cgst":dec(sums["cgst"]),"sgst":dec(sums["sgst"]),"igst":dec(sums["igst"]),
      "cess":dec(sums["cess"]),"total_tax":dec(sums["tax"]),"invoice_total":total,"notes":p.notes}
    try:
        r=db.execute(text("""INSERT INTO fico_gst_invoices(company_id,company_code,registration_id,doc_type,direction,invoice_number,invoice_date,financial_year,
        party_name,party_gstin,place_state_code,supply_category,reverse_charge,is_itc_eligible,taxable_value,cgst,sgst,igst,cess,total_tax,invoice_total,notes)
        VALUES(:company_id,:company_code,:registration_id,:doc_type,:direction,:invoice_number,:invoice_date,:financial_year,
        :party_name,:party_gstin,:place_state_code,:supply_category,:reverse_charge,:is_itc_eligible,:taxable_value,:cgst,:sgst,:igst,:cess,:total_tax,:invoice_total,:notes)"""),vals)
        iid=r.lastrowid
        for x in prepared:
            x["invoice_id"]=iid
            db.execute(text("""INSERT INTO fico_gst_invoice_lines(invoice_id,line_number,description,hsn_sac,quantity,unit,unit_price,discount,taxable_value,gst_rate,cgst,sgst,igst,cess,line_total)
            VALUES(:invoice_id,:line_number,:description,:hsn_sac,:quantity,:unit,:unit_price,:discount,:taxable_value,:gst_rate,:cgst,:sgst,:igst,:cess,:line_total)"""),x)
        db.commit()
        return invoice_detail(db,iid)
    except IntegrityError as e: db.rollback(); raise HTTPException(409,"Invoice number already exists for this company and direction") from e
    except SQLAlchemyError as e: db.rollback(); raise HTTPException(500,"Could not save GST document; check GST table setup") from e

@router.get("/invoices")
def invoices(company_id:int=Query(gt=0),direction:Optional[str]=None,status:Optional[str]=None,
             date_from:Optional[date]=None,date_to:Optional[date]=None,search:Optional[str]=None,
             limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),db:Session=Depends(get_db)):
    company(db,company_id)
    if date_from and date_to and date_to<date_from: raise HTTPException(422,"date_to must be on or after date_from")
    sql="SELECT * FROM fico_gst_invoices WHERE company_id=:cid"; q={"cid":company_id,"limit":limit,"offset":offset}
    for key,val in (("direction",direction),("status",status)):
        if val: sql+=f" AND {key}=: {key}".replace(": ",":"); q[key]=val.upper()
    if date_from: sql+=" AND invoice_date>=:df"; q["df"]=date_from
    if date_to: sql+=" AND invoice_date<=:dt"; q["dt"]=date_to
    if search: sql+=" AND (invoice_number LIKE :s OR party_name LIKE :s OR party_gstin LIKE :s)"; q["s"]=f"%{search}%"
    sql+=" ORDER BY invoice_date DESC,id DESC LIMIT :limit OFFSET :offset"
    return [dict(x) for x in db.execute(text(sql),q).mappings().all()]

@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id:int,db:Session=Depends(get_db)): return invoice_detail(db,invoice_id)

@router.patch("/invoices/{invoice_id}/status")
def update_invoice_status(invoice_id:int,status:Literal["DRAFT","APPROVED","CANCELLED"],db:Session=Depends(get_db)):
    row=db.execute(text("SELECT id,status FROM fico_gst_invoices WHERE id=:id"),{"id":invoice_id}).mappings().first()
    if not row: raise HTTPException(404,"GST document not found")
    if row["status"]=="CANCELLED": raise HTTPException(409,"Cancelled document cannot be changed")
    db.execute(text("UPDATE fico_gst_invoices SET status=:s WHERE id=:id"),{"s":status,"id":invoice_id}); db.commit()
    return invoice_detail(db,invoice_id)

@router.get("/dashboard")
def dashboard(company_id:int=Query(gt=0),date_from:Optional[date]=None,date_to:Optional[date]=None,db:Session=Depends(get_db)):
    company(db,company_id)
    q={"cid":company_id}; where="company_id=:cid AND status<>'CANCELLED'"
    if date_from: where+=" AND invoice_date>=:df"; q["df"]=date_from
    if date_to: where+=" AND invoice_date<=:dt"; q["dt"]=date_to
    if date_from and date_to and date_to<date_from: raise HTTPException(422,"Invalid date range")
    rows=db.execute(text(f"""SELECT direction,COUNT(*) count_docs,COALESCE(SUM(taxable_value),0) taxable,
      COALESCE(SUM(cgst),0) cgst,COALESCE(SUM(sgst),0) sgst,COALESCE(SUM(igst),0) igst,
      COALESCE(SUM(cess),0) cess,COALESCE(SUM(invoice_total),0) total
      FROM fico_gst_invoices WHERE {where} GROUP BY direction"""),q).mappings().all()
    out={r["direction"]:dict(r) for r in rows}
    sales=out.get("SALES",{}); purchase=out.get("PURCHASE",{})
    output=dec(sales.get("cgst",0))+dec(sales.get("sgst",0))+dec(sales.get("igst",0))
    itc=dec(purchase.get("cgst",0))+dec(purchase.get("sgst",0))+dec(purchase.get("igst",0)) if purchase else Decimal("0")
    return {"sales":sales,"purchases":purchase,"output_tax":float(output),"input_tax_recorded":float(itc),
      "net_tax_before_adjustments":float(output-itc),"note":"Indicative management summary; ITC eligibility, reversals, RCM and statutory adjustments require review."}

@router.get("/reports/sales-register")
def sales_register(company_id:int=Query(gt=0),date_from:Optional[date]=None,date_to:Optional[date]=None,db:Session=Depends(get_db)):
    company(db,company_id)
    sql="SELECT * FROM fico_gst_invoices WHERE company_id=:cid AND direction='SALES' AND status<>'CANCELLED'"
    p={"cid":company_id}
    if date_from: sql+=" AND invoice_date>=:a"; p["a"]=date_from
    if date_to: sql+=" AND invoice_date<=:b"; p["b"]=date_to
    sql+=" ORDER BY invoice_date,invoice_number"
    return [dict(x) for x in db.execute(text(sql),p).mappings().all()]

@router.get("/reports/purchase-register")
def purchase_register(company_id:int=Query(gt=0),date_from:Optional[date]=None,date_to:Optional[date]=None,db:Session=Depends(get_db)):
    company(db,company_id)
    sql="SELECT * FROM fico_gst_invoices WHERE company_id=:cid AND direction='PURCHASE' AND status<>'CANCELLED'"
    p={"cid":company_id}
    if date_from: sql+=" AND invoice_date>=:a"; p["a"]=date_from
    if date_to: sql+=" AND invoice_date<=:b"; p["b"]=date_to
    sql+=" ORDER BY invoice_date,invoice_number"
    return [dict(x) for x in db.execute(text(sql,p)).mappings().all()]

@router.get("/reports/itc")
def itc_report(company_id:int=Query(gt=0),date_from:Optional[date]=None,date_to:Optional[date]=None,db:Session=Depends(get_db)):
    company(db,company_id)
    sql="""SELECT COUNT(*) documents,COALESCE(SUM(cgst+sgst+igst+cess),0) tax_total,
      COALESCE(SUM(CASE WHEN is_itc_eligible=1 AND reverse_charge=0 THEN cgst+sgst+igst+cess ELSE 0 END),0) eligible_itc,
      COALESCE(SUM(CASE WHEN is_itc_eligible=0 THEN cgst+sgst+igst+cess ELSE 0 END),0) ineligible_tax
      FROM fico_gst_invoices WHERE company_id=:cid AND direction='PURCHASE' AND status<>'CANCELLED'"""
    p={"cid":company_id}
    if date_from: sql+=" AND invoice_date>=:a"; p["a"]=date_from
    if date_to: sql+=" AND invoice_date<=:b"; p["b"]=date_to
    return dict(db.execute(text(sql),p).mappings().one())

@router.get("/returns/summary")
def return_summary(company_id:int=Query(gt=0),tax_period:str=Query(...,pattern=r"^\d{4}-\d{2}$"),db:Session=Depends(get_db)):
    company(db,company_id)
    start=date.fromisoformat(tax_period+"-01")
    end=date(start.year+1,1,1) if start.month==12 else date(start.year,start.month+1,1)
    sales=db.execute(text("""SELECT COUNT(*) docs,COALESCE(SUM(taxable_value),0) taxable,
      COALESCE(SUM(cgst),0) cgst,COALESCE(SUM(sgst),0) sgst,COALESCE(SUM(igst),0) igst,COALESCE(SUM(cess),0) cess
      FROM fico_gst_invoices WHERE company_id=:cid AND direction='SALES' AND status<>'CANCELLED'
      AND invoice_date>=:a AND invoice_date<:b"""),{"cid":company_id,"a":start,"b":end}).mappings().one()
    purchases=db.execute(text("""SELECT COUNT(*) docs,COALESCE(SUM(taxable_value),0) taxable,
      COALESCE(SUM(cgst),0) cgst,COALESCE(SUM(sgst),0) sgst,COALESCE(SUM(igst),0) igst,COALESCE(SUM(cess),0) cess,
      COALESCE(SUM(CASE WHEN is_itc_eligible=1 AND reverse_charge=0 THEN cgst+sgst+igst ELSE 0 END),0) eligible_itc
      FROM fico_gst_invoices WHERE company_id=:cid AND direction='PURCHASE' AND status<>'CANCELLED'
      AND invoice_date>=:a AND invoice_date<:b"""),{"cid":company_id,"a":start,"b":end}).mappings().one()
    outtax=dec(sales["cgst"])+dec(sales["sgst"])+dec(sales["igst"])
    itc=dec(purchases["eligible_itc"])
    return {"tax_period":tax_period,"gstr1":{"outward_supplies":dict(sales)},"gstr3b":{
      "outward_taxable_value":float(sales["taxable"]),"output_tax":float(outtax),
      "eligible_itc":float(itc),"indicative_net_tax":float(outtax-itc),
      "purchase_summary":dict(purchases)},
      "disclaimer":"Preparation summary only. Validate place of supply, exemptions, RCM, ITC restrictions, amendments and current GSTN schema before filing."}

@router.get("/returns/periods")
def return_periods(company_id:int=Query(gt=0),db:Session=Depends(get_db)):
    company(db,company_id)
    return [dict(x) for x in db.execute(text("SELECT * FROM fico_gst_return_periods WHERE company_id=:cid ORDER BY tax_period DESC"),{"cid":company_id}).mappings().all()]

@router.post("/returns/periods")
def save_return_period(company_id:int=Query(gt=0),financial_year:str=Query(...),tax_period:str=Query(...,pattern=r"^\d{4}-\d{2}$"),db:Session=Depends(get_db)):
    company(db,company_id)
    try:
        db.execute(text("""INSERT INTO fico_gst_return_periods(company_id,financial_year,tax_period)
        VALUES(:cid,:fy,:period) ON DUPLICATE KEY UPDATE financial_year=:fy"""),{"cid":company_id,"fy":financial_year,"period":tax_period})
        db.commit()
        return {"message":"Return period saved","tax_period":tax_period,"status":"OPEN"}
    except SQLAlchemyError as e: db.rollback(); raise HTTPException(500,"Could not save return period") from e

@router.get("/export/invoices.csv")
def export_invoices(company_id:int=Query(gt=0),direction:Optional[str]=None,db:Session=Depends(get_db)):
    company(db,company_id)
    sql="SELECT invoice_number,invoice_date,direction,doc_type,party_name,party_gstin,place_state_code,supply_category,taxable_value,cgst,sgst,igst,cess,total_tax,invoice_total,status FROM fico_gst_invoices WHERE company_id=:cid"
    p={"cid":company_id}
    if direction: sql+=" AND direction=:d"; p["d"]=direction.upper()
    rows=db.execute(text(sql+" ORDER BY invoice_date"),p).mappings().all()
    f=io.StringIO(); w=csv.writer(f); w.writerow(list(rows[0].keys()) if rows else ["invoice_number","invoice_date","direction","doc_type","party_name","party_gstin","place_state_code","supply_category","taxable_value","cgst","sgst","igst","cess","total_tax","invoice_total","status"])
    for row in rows: w.writerow([str(v) if v is not None else "" for v in row.values()])
    f.seek(0)
    return StreamingResponse(iter([f.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=gst_invoices.csv"})

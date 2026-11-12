"""
FinTech Bank Statement Analyzer & Underwriting Dashboard Server
FastAPI Web Application backend.
"""

import os
import json
import sqlite3
import traceback
from contextlib import asynccontextmanager
import pandas as pd
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from analyzer_engine import (
    DB_PATH,
    init_db,
    clear_db,
    detect_bank_format,
    diagnose_pdf,
    parse_federal_pdf,
    parse_hdfc_pdf,
    parse_axis_pdf,
    parse_idfc_pdf,
    parse_sbi_pdf,
    parse_icici_pdf,
    parse_standard_chartered_pdf,
    parse_universal_pdf,
    compute_underwriting_insights,
    save_statement_to_db,
    tag_recurring_salary,
    clean_amount
)
from export_service import create_excel_report, create_los_json

_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip('"').strip("'")
                    if _k and _v and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="FinTech Bank Statement Analyzer & Underwriting Suite", version="3.2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

@app.get("/api/statements")
def get_statements():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM statements ORDER BY id ASC")
    stmts = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"statements": stmts}

@app.get("/api/dashboard")
def get_dashboard(bank: Optional[str] = None):
    conn = get_db_conn()
    c = conn.cursor()
    
    if bank and str(bank).lower() != 'all':
        c.execute("SELECT * FROM statements WHERE bank = ? OR account_no = ?", (bank, bank))
    else:
        c.execute("SELECT * FROM statements ORDER BY id ASC")
    stmts = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT bank, borrower_name, account_no, abb, closing_balance FROM statements")
    all_stmts = [dict(r) for r in c.fetchall()]
    
    if bank and str(bank).lower() != 'all':
        c.execute("SELECT * FROM transactions WHERE bank = ? OR account_no = ? ORDER BY iso_date DESC, id DESC", (bank, bank))
    else:
        c.execute("SELECT * FROM transactions ORDER BY iso_date DESC, id DESC")
    txns = [dict(r) for r in c.fetchall()]
    conn.close()
    
    if not txns:
        return {
            "is_empty": True,
            "selected_bank": bank or "All",
            "available_statements": [],
            "insights": {
                "metadata": {
                    "bank": "No Statement Uploaded",
                    "borrower_name": "—",
                    "account_no": "—",
                    "branch": "—",
                    "sol_id": "—",
                    "ifsc": "—",
                    "cust_id": "—",
                    "email": "—",
                    "mobile": "—",
                    "account_type": "—",
                    "period": "—",
                    "opening_balance": 0.0,
                    "closing_balance": 0.0,
                    "total_withdrawals": 0.0,
                    "total_deposits": 0.0
                },
                "reconciliation": {
                    "opening_balance": 0.0,
                    "total_deposits": 0.0,
                    "total_withdrawals": 0.0,
                    "closing_balance": 0.0,
                    "calculated_closing": 0.0,
                    "variance": 0.0,
                    "is_reconciled": True,
                    "total_transactions": 0,
                    "dr_count": 0,
                    "cr_count": 0
                },
                "salary_audit": {
                    "verified_employers": [],
                    "salary_run_rate": "₹0",
                    "avg_monthly_salary": 0.0,
                    "credits_count": 0,
                    "credits_history": [],
                    "employer_switch": False,
                    "switch_notes": "Awaiting bank statement upload...",
                    "stability_score": 0,
                    "stability_tier": "Awaiting Ingestion"
                },
                "debt_and_emi": {
                    "monthly_obligations": 0.0,
                    "active_lenders": [],
                    "lenders_count": 0,
                    "jumbo_loans_disbursed": 0.0,
                    "jumbo_loans_list": [],
                    "foir_percentage": 0.0,
                    "total_emi_paid": 0.0
                },
                "cc_stress": {
                    "is_alert": False,
                    "severity": "NORMAL",
                    "message": "No statement loaded. Ingest statements to evaluate credit card stress.",
                    "jumbo_disbursed": 0.0,
                    "card_debt_settled": 0.0
                },
                "stock_and_mf": {
                    "total_trading_flow": 0.0,
                    "total_inflows": 0.0,
                    "total_outflows": 0.0,
                    "angel_one_inflows": 0.0,
                    "iccl_sip_outflows": 0.0,
                    "nse_bse_settlements": 0.0,
                    "txns_count": 0
                },
                "cc_and_wallet_churn": {
                    "total_churn_volume": 0.0,
                    "cred_inflows": 0.0,
                    "cred_outflows": 0.0,
                    "net_cred_rotation": 0.0,
                    "cc_payments_total": 0.0,
                    "wallet_topups_total": 0.0,
                    "txns_count": 0
                },
                "bounce_audit": {
                    "bounce_count": 0,
                    "is_clean": True,
                    "status_text": "0 Bounces • Clean Record",
                    "tag_color": "emerald",
                    "bounces_list": []
                },
                "abb_metrics": {
                    "overall_abb": 0.0,
                    "monthly_abb": {},
                    "min_balance": 0.0,
                    "max_balance": 0.0,
                    "days_tracked": 0
                }
            },
            "tab_counts": {
                "all": 0,
                "salary": 0,
                "loans": 0,
                "trading": 0,
                "churn": 0,
                "bounce": 0
            },
            "chart_cashflow": {"labels": [], "inflows": [], "outflows": [], "net": []},
            "chart_categories": {"labels": [], "values": [], "counts": {}},
            "chart_balance": [],
            "total_transactions": 0
        }
        
    if len(stmts) == 1:
        meta = stmts[0]
    else:
        meta = {
            "bank": "Consolidated Portfolio",
            "borrower_name": "Multi-Borrower Portfolio",
            "account_no": "Combined Accounts",
            "branch": "Multi-Branch",
            "sol_id": "—",
            "ifsc": "Multi-IFSC",
            "cust_id": "—",
            "email": "—",
            "mobile": "—",
            "account_type": "Combined Savings & Salary Portfolio",
            "period": "Consolidated Period",
            "opening_balance": sum(s.get('opening_balance', 0.0) for s in stmts),
            "closing_balance": sum(s.get('closing_balance', 0.0) for s in stmts),
            "total_withdrawals": sum(s.get('total_withdrawals', 0.0) for s in stmts),
            "total_deposits": sum(s.get('total_deposits', 0.0) for s in stmts)
        }
        
    insights = compute_underwriting_insights(txns, meta)
    
    df = pd.DataFrame(txns)
    df['month'] = df['iso_date'].str[:7]
    monthly = df.groupby('month')[['credit', 'debit']].sum().reset_index()
    monthly = monthly[monthly['month'].str.len() == 7].sort_values('month')
    monthly['net'] = monthly['credit'] - monthly['debit']
    
    cat_summary = df.groupby('category')[['debit', 'credit']].sum().reset_index()
    cat_counts = df['category'].value_counts().to_dict()
    
    df_sorted = df.sort_values(['iso_date', 'id'])
    step = max(1, len(df_sorted) // 40)
    sampled_balance = df_sorted.iloc[::step][['txn_date', 'iso_date', 'balance', 'bank']].to_dict('records')
    
    # Backward compatible with frontend tab 'churn' while capturing both CC_PAYMENT and WALLET_TOPUP
    tab_counts = {
        "all": len(txns),
        "salary": int(df['category'].str.contains('SALARY|BUSINESS_INFLOW', na=False).sum()),
        "loans": int(df['category'].str.contains('LOAN|EMI', na=False).sum()),
        "trading": int(df['category'].str.contains('STOCK|TRADING', na=False).sum()),
        "churn": int(df['category'].str.contains('CC_PAYMENT|WALLET_TOPUP|CC_WALLET_CHURN', na=False).sum()),
        "bounce": int(df['category'].str.contains('BOUNCE', na=False).sum())
    }
    
    return {
        "is_empty": False,
        "selected_bank": bank or "All",
        "available_statements": all_stmts,
        "insights": insights,
        "tab_counts": tab_counts,
        "chart_cashflow": {
            "labels": monthly['month'].tolist(),
            "inflows": [round(float(v), 2) for v in monthly['credit'].tolist()],
            "outflows": [round(float(v), 2) for v in monthly['debit'].tolist()],
            "net": [round(float(v), 2) for v in monthly['net'].tolist()]
        },
        "chart_categories": {
            "labels": cat_summary['category'].tolist(),
            "values": [round(float(v), 2) for v in cat_summary['debit'].tolist()],
            "counts": cat_counts
        },
        "chart_balance": sampled_balance
    }

@app.get("/api/transactions")
def get_transactions(
    bank: Optional[str] = None,
    tab: str = "all",
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    sort_by: str = "iso_date",
    sort_dir: str = "desc",
    page: int = 1,
    limit: int = 50
):
    conn = get_db_conn()
    c = conn.cursor()
    
    where_clauses = []
    params = []
    
    if bank and str(bank).lower() != 'all':
        where_clauses.append("(bank = ? OR account_no = ?)")
        params.extend([bank, bank])
        
    if tab == "salary":
        where_clauses.append("(category LIKE '%SALARY%' OR category LIKE '%BUSINESS_INFLOW%')")
    elif tab == "loans":
        where_clauses.append("(category LIKE '%LOAN%' OR category LIKE '%EMI%')")
    elif tab == "trading":
        where_clauses.append("(category LIKE '%STOCK%' OR category LIKE '%TRADING%')")
    elif tab in ("churn", "credit_cards", "wallets"):
        where_clauses.append("(category LIKE '%CC_PAYMENT%' OR category LIKE '%WALLET_TOPUP%' OR category LIKE '%CC_WALLET_CHURN%')")
    elif tab == "bounce":
        where_clauses.append("category LIKE '%BOUNCE%'")
        
    if category and str(category).lower() != 'all':
        where_clauses.append("category = ?")
        params.append(category)
        
    if search:
        search_term = f"%{search.strip()}%"
        where_clauses.append("(narration LIKE ? OR txn_date LIKE ? OR bank LIKE ?)")
        params.extend([search_term, search_term, search_term])
        
    if start_date:
        where_clauses.append("iso_date >= ?")
        params.append(start_date)
        
    if end_date:
        where_clauses.append("iso_date <= ?")
        params.append(end_date)
        
    if min_amount is not None:
        where_clauses.append("(debit >= ? OR credit >= ?)")
        params.extend([min_amount, min_amount])
        
    if max_amount is not None:
        where_clauses.append("(debit <= ? OR credit <= ?)")
        params.extend([max_amount, max_amount])
        
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    c.execute(f"SELECT COUNT(*) FROM transactions {where_sql}", params)
    total_records = c.fetchone()[0]
    
    valid_sorts = {"iso_date", "debit", "credit", "balance", "txn_date"}
    sort_column = sort_by if sort_by in valid_sorts else "iso_date"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    
    offset = (page - 1) * limit
    order_sql = f"ORDER BY {sort_column} {direction}, id {direction}"
    query_sql = f"SELECT * FROM transactions {where_sql} {order_sql} LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    c.execute(query_sql, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1
    return {
        "transactions": rows,
        "total": total_records,
        "page": page,
        "limit": limit,
        "total_pages": total_pages
    }

@app.post("/api/reset")
def reset_session():
    clear_db()
    return {"success": True, "message": "Session reset successfully."}

@app.post("/api/upload")
async def upload_statement(
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
    password: Optional[str] = Form(None)
):
    uploaded_files = []
    
    if files:
        uploaded_files.extend(files)
    if file:
        uploaded_files.append(file)
        
    if not uploaded_files:
        raise HTTPException(status_code=400, detail="No PDF file was provided.")
        
    try:
        ingested_statements = []
        failed_info = []
        total_parsed_rows = 0
        
        for upload_item in uploaded_files:
            content = await upload_item.read()
            filename = upload_item.filename
            
            detection = detect_bank_format(file_bytes=content, password=password)
            if detection.get("is_encrypted") and not password:
                return {
                    "success": False,
                    "needs_password": True,
                    "detected_bank": detection.get("detected_bank", "Protected Statement"),
                    "filename": filename,
                    "message": f"'{filename}' is password protected. Please enter password."
                }
            elif detection.get("is_encrypted") and detection.get("auth_failed"):
                return {
                    "success": False,
                    "needs_password": True,
                    "detected_bank": detection.get("detected_bank", "Protected Statement"),
                    "filename": filename,
                    "error": f"Incorrect password for '{filename}'."
                }
                
            bank_name = detection.get("detected_bank", "Generic")
            
            if "Federal" in bank_name:
                records, metadata = parse_federal_pdf(content, password=password)
            elif "SBI" in bank_name or "STATE BANK" in bank_name:
                records, metadata = parse_sbi_pdf(content, password=password)
            elif "Axis" in bank_name:
                records, metadata = parse_axis_pdf(content, password=password)
            elif "HDFC" in bank_name:
                records, metadata = parse_hdfc_pdf(content, password=password)
            elif "IDFC" in bank_name:
                records, metadata = parse_idfc_pdf(content, password=password)
            elif "ICICI" in bank_name:
                records, metadata = parse_icici_pdf(content, password=password)
            elif "Standard Chartered" in bank_name:
                records, metadata = parse_standard_chartered_pdf(content, password=password)
            else:
                records, metadata = parse_universal_pdf(content, password=password, detected_bank=bank_name)

            if not records:
                records, metadata = parse_universal_pdf(content, password=password, detected_bank=bank_name)

            if not records:
                diag = diagnose_pdf(content, password=password)
                print(f"[UPLOAD] no rows parsed from '{filename}' | bank={bank_name} | diag={diag}", flush=True)
                failed_info.append({"file": filename, "bank": bank_name, **diag})
                continue
                
            records = tag_recurring_salary(records)
            insights = compute_underwriting_insights(records, metadata)
            save_statement_to_db(records, metadata, insights, filename=filename)
            
            total_parsed_rows += len(records)
            ingested_statements.append({
                "bank": metadata.get("bank"),
                "borrower_name": metadata.get("borrower_name"),
                "account_no": metadata.get("account_no"),
                "transactions_parsed": len(records),
                "reconciled": insights.get("reconciliation", {}).get("is_reconciled", True),
                "layout_detected": detection.get("layout", "Auto-Detect")
            })
            
        if not ingested_statements:
            hint = ""
            if failed_info:
                f0 = failed_info[0]
                if f0.get("looks_scanned"):
                    hint = " (PDF scanned image format detected - requires OCR)"
                else:
                    hint = f" [bank detected: {f0.get('bank')}, pages: {f0.get('pages')}]"
            raise HTTPException(status_code=400, detail="Could not extract tabular transactions from uploaded statement." + hint)
            
        return {
            "success": True,
            "statements": ingested_statements,
            "total_parsed": total_parsed_rows,
            "bank": ingested_statements[0]["bank"] if len(ingested_statements) == 1 else "Consolidated",
            "borrower_name": ingested_statements[0]["borrower_name"] if len(ingested_statements) == 1 else "Multi-Borrower",
            "transactions_parsed": total_parsed_rows
        }
        
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.get("/api/export/excel")
def export_excel(bank: Optional[str] = None):
    conn = get_db_conn()
    c = conn.cursor()
    
    if bank and str(bank).lower() != 'all':
        c.execute("SELECT * FROM statements WHERE bank = ? OR account_no = ?", (bank, bank))
        stmts = [dict(r) for r in c.fetchall()]
        c.execute("SELECT * FROM transactions WHERE bank = ? OR account_no = ? ORDER BY iso_date DESC, id DESC", (bank, bank))
        txns = [dict(r) for r in c.fetchall()]
    else:
        c.execute("SELECT * FROM statements ORDER BY id ASC")
        stmts = [dict(r) for r in c.fetchall()]
        c.execute("SELECT * FROM transactions ORDER BY iso_date DESC, id DESC")
        txns = [dict(r) for r in c.fetchall()]
    conn.close()
    
    if not txns:
        raise HTTPException(status_code=404, detail="No transactions found to export")
        
    if len(stmts) == 1:
        meta = stmts[0]
    else:
        meta = {
            "bank": "Consolidated Portfolio",
            "borrower_name": "Multi-Borrower Portfolio",
            "account_no": "Combined Accounts",
            "branch": "Multi-Branch",
            "sol_id": "—",
            "ifsc": "Multi-IFSC",
            "cust_id": "—",
            "email": "—",
            "mobile": "—",
            "account_type": "Combined Savings & Salary Portfolio",
            "period": "Consolidated Period",
            "opening_balance": sum(s.get('opening_balance', 0.0) for s in stmts),
            "closing_balance": sum(s.get('closing_balance', 0.0) for s in stmts),
            "total_withdrawals": sum(s.get('total_withdrawals', 0.0) for s in stmts),
            "total_deposits": sum(s.get('total_deposits', 0.0) for s in stmts)
        }
        
    insights = compute_underwriting_insights(txns, meta)
    excel_stream = create_excel_report(insights, txns, bank_filter=bank or "All Statements")
    filename = f"FinTech_Underwriting_Statement_{bank or 'Consolidated'}.xlsx".replace(" ", "_")
    return Response(
        content=excel_stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/export/json")
def export_json(bank: Optional[str] = None):
    conn = get_db_conn()
    c = conn.cursor()
    
    if bank and str(bank).lower() != 'all':
        c.execute("SELECT * FROM statements WHERE bank = ? OR account_no = ?", (bank, bank))
        stmts = [dict(r) for r in c.fetchall()]
        c.execute("SELECT * FROM transactions WHERE bank = ? OR account_no = ? ORDER BY iso_date DESC, id DESC", (bank, bank))
        txns = [dict(r) for r in c.fetchall()]
    else:
        c.execute("SELECT * FROM statements ORDER BY id ASC")
        stmts = [dict(r) for r in c.fetchall()]
        c.execute("SELECT * FROM transactions ORDER BY iso_date DESC, id DESC")
        txns = [dict(r) for r in c.fetchall()]
    conn.close()
    
    if not txns:
        raise HTTPException(status_code=404, detail="No transactions found to export")
        
    if len(stmts) == 1:
        meta = stmts[0]
    else:
        meta = {
            "bank": "Consolidated Portfolio",
            "borrower_name": "Multi-Borrower Portfolio",
            "account_no": "Combined Accounts",
            "branch": "Multi-Branch",
            "sol_id": "—",
            "ifsc": "Multi-IFSC",
            "cust_id": "—",
            "email": "—",
            "mobile": "—",
            "account_type": "Combined Savings & Salary Portfolio",
            "period": "Consolidated Period",
            "opening_balance": sum(s.get('opening_balance', 0.0) for s in stmts),
            "closing_balance": sum(s.get('closing_balance', 0.0) for s in stmts),
            "total_withdrawals": sum(s.get('total_withdrawals', 0.0) for s in stmts),
            "total_deposits": sum(s.get('total_deposits', 0.0) for s in stmts)
        }
        
    insights = compute_underwriting_insights(txns, meta)
    json_str = create_los_json(insights, txns, bank_filter=bank or "All Statements")
    filename = f"LOS_Origination_Payload_{bank or 'Consolidated'}.json".replace(" ", "_")
    return Response(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/", response_class=HTMLResponse)
def index_page():
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>FinTech Bank Statement Analyzer</h1>"

if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="FinTech Bank Statement Analyzer Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)), help="Port to run server on")
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", "0.0.0.0"), help="Host to listen on")
    parser.add_argument("--reload", action="store_true", default=False, help="Enable auto-reload")
    args, unknown = parser.parse_known_args()

    if unknown and unknown[0].isdigit():
        args.port = int(unknown[0])

    print(f"\n=======================================================", flush=True)
    print(f"🚀 FinTech Analyzer Server starting on http://{args.host}:{args.port}", flush=True)
    print(f"📂 Open your browser at: http://{args.host}:{args.port}", flush=True)
    print(f"=======================================================\n", flush=True)

    if args.reload:
        uvicorn.run("server:app", host=args.host, port=args.port, reload=True, reload_excludes=["venv", ".git", "__pycache__"])
    else:
        uvicorn.run(app, host=args.host, port=args.port)
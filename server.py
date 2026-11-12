"""
FinTech Bank Statement Analyzer & Underwriting Dashboard Server
FastAPI Web Application backend.
"""

import os
import sqlite3
import logging
import uuid
from contextlib import asynccontextmanager, closing
from typing import Optional

import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# analyzer_engine loads .env on import, so it must be imported before reading any env var here.
from analyzer_engine import (
    DB_PATH,
    init_db,
    clear_db,
    detect_bank_format,
    diagnose_pdf,
    parse_statement,
    compute_underwriting_insights,
    save_statement_to_db,
    tag_recurring_salary,
    purge_old_cases,
    save_case_ai,
    load_case_ai,
    get_latest_case_id,
    gemini_enabled,
    run_gemini_underwriting_intelligence,
    sanitize_ai_result,
)
from export_service import create_excel_report, create_los_json

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("server")

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="FinTech Bank Statement Analyzer & Underwriting Suite", version="3.3", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------------------

def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _bank_filter(bank):
    if bank and str(bank).lower() != "all":
        return " AND (bank = ? OR account_no = ?)", [bank, bank]
    return "", []


def _resolve_case(case_id: Optional[str]) -> Optional[str]:
    return case_id or get_latest_case_id()


def _load_case(case_id, bank):
    """Return (statements, all_statements, transactions) for one case (optionally one bank/account)."""
    clause, extra = _bank_filter(bank)
    with closing(get_db_conn()) as conn:
        stmts = [dict(r) for r in conn.execute(
            f"SELECT * FROM statements WHERE case_id = ?{clause} ORDER BY id ASC", [case_id] + extra)]
        all_stmts = [dict(r) for r in conn.execute(
            "SELECT id, bank, borrower_name, account_no, abb, closing_balance FROM statements "
            "WHERE case_id = ? ORDER BY id ASC", [case_id])]
        txns = [dict(r) for r in conn.execute(
            f"SELECT * FROM transactions WHERE case_id = ?{clause} ORDER BY iso_date DESC, id DESC",
            [case_id] + extra)]
    return stmts, all_stmts, txns


def _build_meta(stmts):
    if len(stmts) == 1:
        return stmts[0]
    return {
        "bank": "Consolidated Portfolio", "borrower_name": "Multi-Borrower Portfolio",
        "account_no": "Combined Accounts", "branch": "Multi-Branch", "sol_id": "—", "ifsc": "Multi-IFSC",
        "cust_id": "—", "email": "—", "mobile": "—",
        "account_type": "Combined Savings & Salary Portfolio", "period": "Consolidated Period",
        "opening_balance": sum(s.get("opening_balance", 0.0) for s in stmts),
        "closing_balance": sum(s.get("closing_balance", 0.0) for s in stmts),
        "total_withdrawals": sum(s.get("total_withdrawals", 0.0) for s in stmts),
        "total_deposits": sum(s.get("total_deposits", 0.0) for s in stmts),
    }


def _insights_for(case_id, bank, stmts, txns):
    meta = _build_meta(stmts)
    # The stored AI result describes the whole case, so it is only used for the unfiltered view.
    use_ai = not (bank and str(bank).lower() != "all")
    ai = load_case_ai(case_id) if use_ai else None
    return compute_underwriting_insights(txns, meta, ai_result=ai, statements=stmts)


def _empty_dashboard(bank, case_id=None):
    return {
        "is_empty": True,
        "case_id": case_id,
        "selected_bank": bank or "All",
        "available_statements": [],
        "insights": {
            "metadata": {
                "bank": "No Statement Uploaded", "borrower_name": "—", "account_no": "—", "branch": "—",
                "sol_id": "—", "ifsc": "—", "cust_id": "—", "email": "—", "mobile": "—",
                "account_type": "—", "period": "—", "opening_balance": 0.0, "closing_balance": 0.0,
                "total_withdrawals": 0.0, "total_deposits": 0.0,
            },
            "reconciliation": {
                "opening_balance": 0.0, "total_deposits": 0.0, "total_withdrawals": 0.0, "closing_balance": 0.0,
                "calculated_closing": 0.0, "variance": 0.0, "is_reconciled": True, "total_transactions": 0,
                "dr_count": 0, "cr_count": 0, "parse_gaps": 0, "printed_totals_match": None, "per_statement": [],
            },
            "salary_audit": {
                "verified_employers": [], "salary_run_rate": "₹0", "avg_monthly_salary": 0.0, "credits_count": 0,
                "credits_history": [], "employer_switch": False,
                "switch_notes": "Awaiting bank statement upload...", "stability_score": 0,
                "stability_tier": "Awaiting Ingestion",
            },
            "debt_and_emi": {
                "monthly_obligations": 0.0, "active_lenders": [], "lenders_count": 0, "jumbo_loans_disbursed": 0.0,
                "jumbo_loans_list": [], "foir_percentage": None, "total_emi_paid": 0.0,
            },
            "cc_stress": {
                "is_alert": False, "severity": "NORMAL",
                "message": "No statement loaded. Ingest statements to evaluate credit card stress.",
                "jumbo_disbursed": 0.0, "card_debt_settled": 0.0,
            },
            "stock_and_mf": {
                "total_trading_flow": 0.0, "total_inflows": 0.0, "total_outflows": 0.0, "angel_one_inflows": 0.0,
                "iccl_sip_outflows": 0.0, "nse_bse_settlements": 0.0, "txns_count": 0,
            },
            "cc_and_wallet_churn": {
                "total_churn_volume": 0.0, "cred_inflows": 0.0, "cred_outflows": 0.0, "net_cred_rotation": 0.0,
                "cc_payments_total": 0.0, "wallet_topups_total": 0.0, "txns_count": 0,
            },
            "bounce_audit": {
                "bounce_count": 0, "is_clean": True, "status_text": "0 Bounces • Clean Record",
                "tag_color": "emerald", "bounces_list": [],
            },
            "abb_metrics": {"overall_abb": 0.0, "monthly_abb": {}, "min_balance": 0.0, "max_balance": 0.0, "days_tracked": 0},
        },
        "tab_counts": {"all": 0, "salary": 0, "loans": 0, "trading": 0, "churn": 0, "bounce": 0},
        "chart_cashflow": {"labels": [], "inflows": [], "outflows": [], "net": []},
        "chart_categories": {"labels": [], "values": [], "counts": {}},
        "chart_balance": [],
        "total_transactions": 0,
    }


# --------------------------------------------------------------------------------------
# Read APIs  (case_id is optional everywhere; default = most recent upload)
# --------------------------------------------------------------------------------------

@app.get("/api/statements")
def get_statements(case_id: Optional[str] = None):
    case_id = _resolve_case(case_id)
    if not case_id:
        return {"case_id": None, "statements": []}
    with closing(get_db_conn()) as conn:
        stmts = [dict(r) for r in conn.execute(
            "SELECT * FROM statements WHERE case_id = ? ORDER BY id ASC", (case_id,))]
    return {"case_id": case_id, "statements": stmts}


@app.get("/api/dashboard")
def get_dashboard(bank: Optional[str] = None, case_id: Optional[str] = None):
    case_id = _resolve_case(case_id)
    if not case_id:
        return _empty_dashboard(bank)
    stmts, all_stmts, txns = _load_case(case_id, bank)
    if not txns:
        return _empty_dashboard(bank, case_id)

    insights = _insights_for(case_id, bank, stmts, txns)

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

    tab_counts = {
        "all": len(txns),
        "salary": int(df['category'].str.contains('SALARY|BUSINESS_INFLOW', na=False).sum()),
        "loans": int(df['category'].str.contains('LOAN|EMI', na=False).sum()),
        "trading": int(df['category'].str.contains('STOCK|TRADING', na=False).sum()),
        "churn": int(df['category'].str.contains('CC_PAYMENT|WALLET_TOPUP|CC_WALLET_CHURN', na=False).sum()),
        "bounce": int(df['category'].str.contains('BOUNCE', na=False).sum()),
    }

    return {
        "is_empty": False,
        "case_id": case_id,
        "selected_bank": bank or "All",
        "available_statements": all_stmts,
        "insights": insights,
        "tab_counts": tab_counts,
        "chart_cashflow": {
            "labels": monthly['month'].tolist(),
            "inflows": [round(float(v), 2) for v in monthly['credit'].tolist()],
            "outflows": [round(float(v), 2) for v in monthly['debit'].tolist()],
            "net": [round(float(v), 2) for v in monthly['net'].tolist()],
        },
        "chart_categories": {
            "labels": cat_summary['category'].tolist(),
            "values": [round(float(v), 2) for v in cat_summary['debit'].tolist()],
            "counts": cat_counts,
        },
        "chart_balance": sampled_balance,
    }


@app.get("/api/transactions")
def get_transactions(
    bank: Optional[str] = None,
    case_id: Optional[str] = None,
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
    limit: int = 50,
):
    case_id = _resolve_case(case_id)
    page, limit = max(1, page), max(1, min(limit, 500))
    if not case_id:
        return {"transactions": [], "total": 0, "page": page, "limit": limit, "total_pages": 1}

    where, params = ["case_id = ?"], [case_id]
    if bank and str(bank).lower() != 'all':
        where.append("(bank = ? OR account_no = ?)")
        params.extend([bank, bank])

    if tab == "salary":
        where.append("(category LIKE '%SALARY%' OR category LIKE '%BUSINESS_INFLOW%')")
    elif tab == "loans":
        where.append("(category LIKE '%LOAN%' OR category LIKE '%EMI%')")
    elif tab == "trading":
        where.append("(category LIKE '%STOCK%' OR category LIKE '%TRADING%')")
    elif tab in ("churn", "credit_cards", "wallets"):
        where.append("(category LIKE '%CC_PAYMENT%' OR category LIKE '%WALLET_TOPUP%' OR category LIKE '%CC_WALLET_CHURN%')")
    elif tab == "bounce":
        where.append("category LIKE '%BOUNCE%'")

    if category and str(category).lower() != 'all':
        where.append("category = ?")
        params.append(category)
    if search:
        term = f"%{search.strip()}%"
        where.append("(narration LIKE ? OR txn_date LIKE ? OR bank LIKE ?)")
        params.extend([term, term, term])
    if start_date:
        where.append("iso_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("iso_date <= ?")
        params.append(end_date)
    if min_amount is not None:
        where.append("(debit >= ? OR credit >= ?)")
        params.extend([min_amount, min_amount])
    if max_amount is not None:
        where.append("(debit <= ? OR credit <= ?)")
        params.extend([max_amount, max_amount])

    where_sql = "WHERE " + " AND ".join(where)
    sort_column = sort_by if sort_by in {"iso_date", "debit", "credit", "balance", "txn_date"} else "iso_date"
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    with closing(get_db_conn()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transactions {where_sql}", params).fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM transactions {where_sql} ORDER BY {sort_column} {direction}, id {direction} LIMIT ? OFFSET ?",
            params + [limit, (page - 1) * limit])]

    return {
        "transactions": rows, "total": total, "page": page, "limit": limit,
        "total_pages": (total + limit - 1) // limit if total > 0 else 1,
    }


@app.post("/api/reset")
def reset_session():
    clear_db()
    return {"success": True, "message": "Session reset successfully."}


# --------------------------------------------------------------------------------------
# Upload (plain `def`: FastAPI runs it in a threadpool, so heavy PDF parsing does not block the loop)
# --------------------------------------------------------------------------------------

def _read_limited(upload: UploadFile) -> bytes:
    data = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"'{upload.filename}' exceeds the {MAX_UPLOAD_MB} MB limit.")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail=f"'{upload.filename}' is not a PDF file.")
    return data


@app.post("/api/upload")
def upload_statement(
    file: Optional[UploadFile] = File(None),
    files: Optional[list[UploadFile]] = File(None),
    password: Optional[str] = Form(None),
    case_id: Optional[str] = Form(None),
):
    uploads = list(files or [])
    if file:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=400, detail="No PDF file was provided.")

    try:
        # Phase 1: read + validate everything first, so a password prompt never leaves a half-saved case.
        staged = []
        for item in uploads:
            content = _read_limited(item)
            detection = detect_bank_format(file_bytes=content, password=password)
            if detection.get("is_encrypted") and not password:
                return {"success": False, "needs_password": True,
                        "detected_bank": detection.get("detected_bank", "Protected Statement"),
                        "filename": item.filename,
                        "message": f"'{item.filename}' is password protected. Please enter password."}
            if detection.get("is_encrypted") and detection.get("auth_failed"):
                return {"success": False, "needs_password": True,
                        "detected_bank": detection.get("detected_bank", "Protected Statement"),
                        "filename": item.filename, "error": f"Incorrect password for '{item.filename}'."}
            staged.append((item.filename, content, detection))

        # Phase 2: parse + save. All files of this request share one case.
        case_id = case_id or uuid.uuid4().hex[:12]
        ingested, failed, warnings, total_rows = [], [], [], 0

        for filename, content, detection in staged:
            parsed = parse_statement(content, password=password, detection=detection)
            if not parsed:
                diag = diagnose_pdf(content, password=password)
                logger.warning("no rows parsed | bank=%s | pages=%s text_chars=%s scanned=%s",
                               detection.get("detected_bank"), diag.get("pages"), diag.get("text_chars"),
                               diag.get("looks_scanned"))
                failed.append({"file": filename, "bank": detection.get("detected_bank"), **diag})
                continue

            records, metadata, parser_name, quality = parsed
            records = tag_recurring_salary(records)
            insights = compute_underwriting_insights(records, metadata)
            _, _ = save_statement_to_db(records, metadata, insights, filename=filename, case_id=case_id)

            reconciled = insights.get("reconciliation", {}).get("is_reconciled", True)
            if not reconciled:
                warnings.append(
                    f"'{filename}' did not reconcile (variance ₹{quality['variance_paise'] / 100:,.2f}, "
                    f"{quality['gaps']} parse gap(s)). Some rows may be missing - verify before using the numbers.")
            total_rows += len(records)
            ingested.append({
                "filename": filename, "bank": metadata.get("bank"), "borrower_name": metadata.get("borrower_name"),
                "account_no": metadata.get("account_no"), "transactions_parsed": len(records),
                "reconciled": reconciled, "parser": parser_name,
                "layout_detected": detection.get("layout", "Auto-Detect"),
            })

        if not ingested:
            hint = ""
            if failed:
                f0 = failed[0]
                hint = (" (PDF looks like a scanned image - requires OCR)" if f0.get("looks_scanned")
                        else f" [bank detected: {f0.get('bank')}, pages: {f0.get('pages')}]")
            raise HTTPException(status_code=400, detail="Could not extract tabular transactions from uploaded statement." + hint)

        # LLM layer: opt-in, runs ONCE per upload, output validated and stored (dashboard never calls it).
        if gemini_enabled():
            _, _, case_txns = _load_case(case_id, None)
            ai = sanitize_ai_result(run_gemini_underwriting_intelligence(case_txns), case_txns)
            if ai:
                save_case_ai(case_id, ai)

        purge_old_cases(keep=int(os.environ.get("MAX_CASES", "5")))

        return {
            "success": True,
            "case_id": case_id,
            "statements": ingested,
            "failed_files": [f["file"] for f in failed],
            "warnings": warnings,
            "total_parsed": total_rows,
            "bank": ingested[0]["bank"] if len(ingested) == 1 else "Consolidated",
            "borrower_name": ingested[0]["borrower_name"] if len(ingested) == 1 else "Multi-Borrower",
            "transactions_parsed": total_rows,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Upload processing failed")
        raise HTTPException(status_code=500, detail="Processing failed. Please check the server logs.")


# --------------------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------------------

def _export_inputs(bank, case_id):
    case_id = _resolve_case(case_id)
    if not case_id:
        raise HTTPException(status_code=404, detail="No transactions found to export")
    stmts, _, txns = _load_case(case_id, bank)
    if not txns:
        raise HTTPException(status_code=404, detail="No transactions found to export")
    return _insights_for(case_id, bank, stmts, txns), txns


@app.get("/api/export/excel")
def export_excel(bank: Optional[str] = None, case_id: Optional[str] = None):
    insights, txns = _export_inputs(bank, case_id)
    stream = create_excel_report(insights, txns, bank_filter=bank or "All Statements")
    filename = f"FinTech_Underwriting_Statement_{bank or 'Consolidated'}.xlsx".replace(" ", "_")
    return Response(
        content=stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/json")
def export_json(bank: Optional[str] = None, case_id: Optional[str] = None):
    insights, txns = _export_inputs(bank, case_id)
    json_str = create_los_json(insights, txns, bank_filter=bank or "All Statements")
    filename = f"LOS_Origination_Payload_{bank or 'Consolidated'}.json".replace(" ", "_")
    return Response(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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

    logger.info("FinTech Analyzer Server starting on http://%s:%s", args.host, args.port)

    if args.reload:
        uvicorn.run("server:app", host=args.host, port=args.port, reload=True, reload_excludes=["venv", ".git", "__pycache__"])
    else:
        uvicorn.run(app, host=args.host, port=args.port)

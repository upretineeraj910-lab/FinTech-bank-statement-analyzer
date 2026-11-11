"""
End-to-end API test.

Usage:
    python test_endpoints.py path/to/statement.pdf [second.pdf ...]
"""
import os
import sys

from fastapi.testclient import TestClient
from server import app

pdfs = sys.argv[1:] or ["HH.pdf"]
missing = [p for p in pdfs if not os.path.exists(p)]
if missing:
    sys.exit(f"PDF not found: {missing}. Pass the path: python test_endpoints.py your.pdf")

client = TestClient(app)

print("--- Step 0: Reset ---")
assert client.post("/api/reset").status_code == 200

print("--- Step 1: Verify Initial Clean State ---")
data = client.get("/api/dashboard").json()
assert data["is_empty"] is True
assert len(data["available_statements"]) == 0
assert data["tab_counts"]["all"] == 0
print("PASS: clean initial state.")

print("\n--- Step 2: Upload ---")
accounts = set()
for p in pdfs:
    with open(p, "rb") as f:
        r = client.post("/api/upload", files={"file": (os.path.basename(p), f, "application/pdf")})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["success"] is True, res
    st = res["statements"][0]
    accounts.add(st["account_no"])
    print(f"PASS: {p} -> {st['bank']} | A/C {st['account_no']} | {res['total_parsed']} rows | reconciled={st['reconciled']}")

print("\n--- Step 3: Dashboard ---")
data = client.get("/api/dashboard").json()
assert data["is_empty"] is False
assert len(data["available_statements"]) == 1, "upload replaces previous statement"
ins = data["insights"]
print(f"PASS: {len(data['available_statements'])} statement(s) | salary credits: {ins['salary_audit']['credits_count']} "
      f"| employers: {ins['salary_audit']['verified_employers']} | reconciled: {ins['reconciliation']['is_reconciled']}")

print("\n--- Step 4: Exports ---")
assert client.get("/api/export/excel").status_code == 200
assert client.get("/api/export/json").status_code == 200
print("PASS: excel + json export.")

print("\n--- Step 5: Reset ---")
assert client.post("/api/reset").status_code == 200
assert client.get("/api/dashboard").json()["is_empty"] is True
print("PASS: session reset.")
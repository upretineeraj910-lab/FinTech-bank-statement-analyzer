from server import app
from fastapi.testclient import TestClient

client = TestClient(app)

print("--- Step 1: Verify Initial Clean State ---")
r = client.get("/api/dashboard")
assert r.status_code == 200
data = r.json()
assert data["is_empty"] is True
assert len(data["available_statements"]) == 0
assert data["tab_counts"]["all"] == 0
print("PASS: Clean initial state verified (₹0 metrics, no pre-loaded statements).")

print("\n--- Step 2: Upload / Ingest On-Demand ---")
with open("HH.pdf", "rb") as f:
    r = client.post("/api/upload", files={"file": ("HH.pdf", f, "application/pdf")})
    assert r.status_code == 200
    res = r.json()
    assert res["success"] is True
    print(f"PASS: Ingestion successful! Parsed {res['total_parsed']} records.")

print("\n--- Step 3: Verify Populated Dashboard & Dynamic Switcher ---")
r = client.get("/api/dashboard")
data = r.json()
assert data["is_empty"] is False
assert len(data["available_statements"]) == 1
assert data["available_statements"][0]["bank"] == "HDFC Bank"
print(f"PASS: Dashboard switched to populated state with bank: {data['available_statements'][0]['bank']}")

print("\n--- Step 4: Verify Session Reset ---")
r = client.post("/api/reset")
assert r.status_code == 200
r = client.get("/api/dashboard")
data = r.json()
assert data["is_empty"] is True
assert len(data["available_statements"]) == 0
print("PASS: Session successfully reset to clean state.")

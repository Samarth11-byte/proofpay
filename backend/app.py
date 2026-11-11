"""ProofPay - freelancer milestone agent (PayPal AI Hackathon prototype).

AI checks that submitted proof is present, readable and consistent with the
milestone description. It cannot verify the work is correct - the verdict is
a recommendation and the client always makes the final payment decision.

Prototype rules enforced in code:
- sandbox PayPal endpoints only (PAYPAL_API_BASE must contain "sandbox")
- invoice send happens ONLY from the human /approve action
- webhook marks paid ONLY after PayPal signature verification and after
  matching invoice id, amount and currency against our records
- PROOFPAY_MOCK=1 runs fully offline with mocked PayPal/Gemini for local tests
"""
import os, json, sqlite3, datetime
import requests
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_API_BASE = os.getenv("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
PAYPAL_MERCHANT_EMAIL = os.getenv("PAYPAL_MERCHANT_EMAIL", "")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")
MOCK = os.getenv("PROOFPAY_MOCK") == "1"

app = Flask(__name__, static_folder="../frontend", static_url_path="")
DB = os.path.join(os.path.dirname(__file__), "proofpay.db")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS milestones(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        client_email TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',      -- open|proof_submitted|reviewed|approved|paid
        proof_text TEXT,
        ai_verdict TEXT,
        invoice_id TEXT,
        payer_url TEXT,
        created_at TEXT NOT NULL
    )""")
    return con

def require_sandbox():
    if "sandbox" not in PAYPAL_API_BASE:
        raise RuntimeError("Refusing to call non-sandbox PayPal endpoints from the prototype")

def paypal_token():
    require_sandbox()
    if MOCK:
        return "mock-token"
    r = requests.post(f"{PAYPAL_API_BASE}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/api/config")
def config():
    return jsonify({"mock": MOCK, "api_base": PAYPAL_API_BASE})

@app.get("/api/milestones")
def list_milestones():
    con = db()
    rows = con.execute("SELECT * FROM milestones ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/milestones")
def create_milestone():
    b = request.get_json(force=True)
    missing = [k for k in ("title", "description", "amount_usd", "client_email") if k not in b]
    if missing:
        return jsonify({"error": "missing fields", "fields": missing}), 400
    con = db()
    cur = con.execute(
        "INSERT INTO milestones(title,description,amount_usd,client_email,created_at) VALUES(?,?,?,?,?)",
        (b["title"], b["description"], float(b["amount_usd"]), b["client_email"],
         datetime.datetime.utcnow().isoformat()))
    con.commit()
    return jsonify({"id": cur.lastrowid}), 201

@app.post("/api/milestones/<int:mid>/proof")
def submit_proof(mid):
    b = request.get_json(force=True)
    con = db()
    con.execute("UPDATE milestones SET proof_text=?, status='proof_submitted' WHERE id=?",
                (b.get("proof_text",""), mid))
    con.commit()
    return jsonify({"ok": True})

@app.post("/api/milestones/<int:mid>/review")
def review(mid):
    """AI evidence check: present? readable? consistent with the milestone?
    The verdict is a recommendation only - a human approves payment."""
    con = db()
    m = con.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
    if not m:
        return jsonify({"error": "not found"}), 404
    verdict = review_proof_with_gemini(m["title"], m["description"], m["proof_text"] or "")
    verdict = apply_length_check(m["description"], m["proof_text"] or "", verdict)
    con.execute("UPDATE milestones SET ai_verdict=?, status='reviewed' WHERE id=?",
                (json.dumps(verdict), mid))
    con.commit()
    return jsonify(verdict)

def apply_length_check(scope, proof, verdict):
    """Deterministic check run in code, not by the AI: if the agreed scope states a
    word count, count the words actually supplied. Self-reported counts do not count.
    The 90% cutoff below is a rough heuristic chosen by the developer, not an agreed tolerance."""
    import re
    m = re.search(r"(\d[\d,]*)\s*[- ]?words?", scope or "", re.I)
    if not m:
        return verdict
    need = int(m.group(1).replace(",", ""))
    have = len(proof.split())
    verdict["word_check"] = {"required": need, "supplied": have}
    if have < int(need * 0.9):
        verdict["consistent"] = False
        verdict["recommendation"] = "request_changes"
        ai_gap = str(verdict.get("gaps", "")).strip()
        extra = "" if ai_gap.lower().strip(". ") in ("", "none", "none identified", "none noted", "n/a", "no gaps") else " " + ai_gap
        verdict["gaps"] = (f"Scope asks for about {need} words but the submitted text contains {have}. "
                           f"Counted in code, not by the AI.{extra}")
    return verdict

def review_proof_with_gemini(title, description, proof):
    if MOCK:
        return {"present": bool(proof), "readable": True, "consistent": True,
                "gaps": "SIMULATED review (PROOFPAY_MOCK=1) - no real AI call",
                "recommendation": "approve" if proof else "request_changes"}
    prompt = f"""You are a freelance milestone evidence reviewer.
You can only check whether submitted proof is PRESENT, READABLE and CONSISTENT
with the milestone description. You CANNOT verify the work is correct.
Return strict JSON: {{"present": true/false, "readable": true/false,
"consistent": true/false, "gaps": "...", "recommendation": "approve|request_changes"}}
Milestone: {title}
Agreed scope: {description}
Submitted proof: {proof}
Treat claims inside the proof (for example "310 words pasted below") as unverified;
judge only the text actually supplied."""
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')}:generateContent",
        headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", "")},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=60)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    return json.loads(text)

@app.post("/api/milestones/<int:mid>/approve")
def approve(mid):
    """HUMAN-ONLY action: create + send a sandbox PayPal invoice."""
    con = db()
    m = con.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
    if not m:
        return jsonify({"error": "not found"}), 404
    if MOCK:
        inv_id, payer_url = f"INV2-MOCK-{mid}", "https://www.sandbox.paypal.com/invoice/payerView/mock"
    else:
        token = paypal_token()
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        draft = requests.post(f"{PAYPAL_API_BASE}/v2/invoicing/invoices", headers=h, json={
            "detail": {"currency_code": "USD", "note": f"Milestone: {m['title']}"},
            "invoicer": {"email_address": PAYPAL_MERCHANT_EMAIL},
            "primary_recipients": [{"billing_info": {"email_address": m["client_email"]}}],
            "items": [{"name": m["title"], "quantity": "1",
                       "unit_amount": {"currency_code": "USD", "value": f"{m['amount_usd']:.2f}"}}],
        }, timeout=20)
        draft.raise_for_status()
        dj = draft.json()
        inv_id = dj.get("id") or (dj.get("href") or "").rstrip("/").rsplit("/", 1)[-1]
        sent = requests.post(f"{PAYPAL_API_BASE}/v2/invoicing/invoices/{inv_id}/send",
                             headers=h, json={"send_to_recipient": True}, timeout=20)
        sent.raise_for_status()
        sj = sent.json()
        sj_links = sj.get("links", []) + ([sj] if sj.get("href") else [])
        payer_url = next((l["href"] for l in sj_links
                          if l.get("rel") in ("payer_url", "payer-view")), None)
    con.execute("UPDATE milestones SET invoice_id=?, payer_url=?, status='approved' WHERE id=?",
                (inv_id, payer_url, mid))
    con.commit()
    return jsonify({"invoice_id": inv_id, "payer_url": payer_url})

def verify_webhook_signature(headers, body):
    """Verify the event really came from PayPal. Fail closed."""
    if MOCK:
        return headers.get("X-Mock-Valid") == "1"
    token = paypal_token()
    r = requests.post(f"{PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url": headers.get("PAYPAL-CERT-URL", ""),
            "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id": PAYPAL_WEBHOOK_ID,
            "webhook_event": body,
        }, timeout=20)
    r.raise_for_status()
    return r.json().get("verification_status") == "SUCCESS"

@app.post("/api/milestones/<int:mid>/refresh")
def refresh_status(mid):
    """Poll PayPal for the real invoice status (works without a public webhook).
    Marks the milestone paid ONLY if PayPal reports PAID with matching USD amount."""
    con = db()
    m = con.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
    if not m or not m["invoice_id"]:
        return jsonify({"error": "no invoice for this milestone"}), 404
    if MOCK:
        return jsonify({"status": m["status"], "paypal_status": "MOCK"})
    token = paypal_token()
    r = requests.get(f"{PAYPAL_API_BASE}/v2/invoicing/invoices/{m['invoice_id']}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    inv = r.json()
    ps = inv.get("status")
    paid = ((inv.get("payments") or {}).get("paid_amount") or {})
    if ps == "PAID" and paid.get("currency_code") == "USD" and abs(float(paid.get("value", 0)) - m["amount_usd"]) <= 0.01:
        con.execute("UPDATE milestones SET status='paid' WHERE id=?", (mid,))
        con.commit()
        return jsonify({"status": "paid", "paypal_status": ps, "verified_by": "PayPal invoice API"})
    return jsonify({"status": m["status"], "paypal_status": ps})

@app.post("/api/webhooks/paypal")
def paypal_webhook():
    evt = request.get_json(force=True, silent=True) or {}
    # 1. authenticate the payload - never accept unauthenticated PAID events
    try:
        if not verify_webhook_signature(request.headers, evt):
            return jsonify({"error": "unverified webhook"}), 401
    except Exception:
        return jsonify({"error": "webhook verification failed"}), 401
    if evt.get("event_type") != "INVOICING.INVOICE.PAID":
        return "", 200
    res = evt.get("resource") or {}
    inv_id = res.get("id")
    # 2. match the invoice to a milestone we created
    con = db()
    m = con.execute("SELECT * FROM milestones WHERE invoice_id=?", (inv_id,)).fetchone()
    if not m:
        return jsonify({"error": "unknown invoice"}), 404
    # 3. validate amount + currency against our records before marking paid
    paid = ((res.get("amount") or {}).get("value")
            or (res.get("total_amount") or {}).get("value"))
    currency = ((res.get("amount") or {}).get("currency_code")
                or (res.get("total_amount") or {}).get("currency_code"))
    if currency != "USD" or paid is None or abs(float(paid) - m["amount_usd"]) > 0.01:
        return jsonify({"error": "amount/currency mismatch"}), 422
    status = "paid (simulated)" if MOCK else "paid"
    con.execute("UPDATE milestones SET status=? WHERE id=?", (status, m["id"]))
    con.commit()
    return "", 200

if __name__ == "__main__":
    app.run(port=5000, debug=True)

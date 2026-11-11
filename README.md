# ProofPay

A small freelancer milestone helper built for the PayPal AI Hackathon.

A freelancer submits proof text for a milestone. An AI (Google Gemini) reads
that text and gives a recommendation: is it present, readable and consistent
with the agreed scope. A deterministic word count runs in code when the scope
states a length. A human then decides whether to approve. Approving sends a
PayPal **sandbox** invoice to the client. The client pays that invoice
separately in PayPal. The app reads the invoice status back from PayPal's
Invoicing API when you press "Refresh status".

**Everything here uses PayPal sandbox test money and made-up data.**

## What the AI does and does not do
- It reads the proof **text only**. It does not open files, images or links, so
  a list of file names is a self-reported claim.
- It recommends. It never sends an invoice or moves money.
- Word counts for scopes like "300 words" are counted in code, not by the AI.
  The 90% cutoff is a rough developer heuristic, not a standard.

## Roles
1. Freelancer creates a milestone and submits proof text.
2. AI check gives a recommendation on the text.
3. You (the approver) click "Approve & send sandbox invoice". This creates and
   sends the PayPal invoice. It does not pay it.
4. The client pays the invoice separately in PayPal. "Refresh status" then
   marks the milestone paid only if PayPal reports PAID with a matching USD amount.

## Stack
- Flask (Python) backend, plain HTML/JS frontend, SQLite
- Google Gemini REST API (model `gemini-3.1-flash-lite`, override with `GEMINI_MODEL`)
- PayPal sandbox: OAuth, Invoicing API v2 (create, send, get)

## Run it
1. `python -m venv .venv && . .venv/bin/activate`
2. `pip install -r backend/requirements.txt`
3. Copy `backend/.env.example` to `backend/.env` and fill in your own sandbox
   PayPal client id and secret, the sandbox business account email, and a Gemini API key.
   Never commit `.env`.
4. `cd backend && flask --app app run -p 5000`, then open http://127.0.0.1:5000
5. Create a milestone, save proof text, run the AI check, approve. Open the
   "client pay link" and pay as a sandbox personal (buyer) account. Press
   "Refresh status from PayPal".

Offline test mode: `PROOFPAY_MOCK=1` replaces PayPal and Gemini with simulated
responses, labelled SIMULATED. It is for local testing only and is not what the demo shows.

## Tested
A full run against the real sandbox with synthetic data: Gemini check, human
approve, invoice created and sent, paid by a sandbox buyer on PayPal's page,
and Refresh flipped the same milestone to paid ($5.00 USD sandbox).

## Known limits
- **Webhook not verified.** A webhook endpoint exists in the code
  (`/api/webhooks/paypal`, with signature verification), but it was never
  tested against real PayPal events because that needs a public HTTPS URL.
  The demo uses manual Refresh instead.
- **India sandbox accounts.** PayPal blocked invoices between two India
  sandbox accounts (`INR_FOREIGN_CURRENCY_BLOCKED`). The test used an India
  sandbox seller and a US sandbox buyer. This does not show invoicing works for India-based production use.
- The AI does not inspect files. Real file inspection is not part of this project.
- No authentication or multi-user support. This is a demo, not a product.

## License
MIT. See `LICENSE`.

#!/usr/bin/env bash
# ProofPay mock-mode smoke test. Runs the full agent loop offline.
# Usage: cd backend && bash smoke_test.sh
set -e
cd "$(dirname "$0")"
rm -f proofpay.db
PROOFPAY_MOCK=1 python app.py & SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 4
echo "== config =="
curl -s localhost:5000/api/config
echo; echo "== create milestone =="
curl -s -X POST localhost:5000/api/milestones -H 'Content-Type: application/json' \
  -d '{"title":"Logo draft","description":"SVG logo, brand colors","amount_usd":120,"client_email":"client@example.com"}'
echo; echo "== submit proof =="
curl -s -X POST localhost:5000/api/milestones/1/proof -H 'Content-Type: application/json' \
  -d '{"proof_text":"https://example.com/logo.svg plus design notes"}'
echo; echo "== AI review (SIMULATED in mock mode) =="
curl -s -X POST localhost:5000/api/milestones/1/review
echo; echo "== approve -> invoice =="
curl -s -X POST localhost:5000/api/milestones/1/approve
echo; echo "== final state =="
curl -s localhost:5000/api/milestones
echo

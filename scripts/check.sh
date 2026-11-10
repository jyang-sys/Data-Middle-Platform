#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m unittest discover -s tests -v
node --check app/static/app.js
node --check app/static/management.js
node --check app/static/reports.js
node --check app/static/inventory.js
node --check app/static/performance.js
node --check app/static/auth.js
node --check app/static/dashboard.js
node --check app/static/snapshot-reports.js
node --check app/static/base-test-bootstrap.js
node scripts/check-base-test-worker.mjs
node --check app/hosted/shop-settings.mjs
node scripts/check-shop-settings.mjs
node scripts/check-shop-sources.mjs
node scripts/check-people-settings.mjs

#!/usr/bin/env bash
# Operator provisioning for the JellieRAG broker Worker and its Cloudflare
# resources. Run once from apps/broker after `wrangler login`.
#
# This script performs the infra steps that require live Cloudflare credentials
# (tasks 2.1, 2.2, 2.4). It is safe to re-run: resource creation is idempotent
# (existing resources are reported as-is) and the secret is re-prompted.
#
#   2.1  wrangler vectorize create jellyfin-index --dimensions=384 --metric=cosine
#   2.2  wrangler d1 create jellyrag
#   2.3  apply the D1 migration (see migrations/0001_initial_schema.sql)
#   2.4  provision BROKER_SECRET via wrangler secret put
#   2.5  pin/verify the Llama model slug (manual catalog check — see note below)
set -euo pipefail

cd "$(dirname "$0")"

echo "== 2.1 Creating Vectorize index (384-dim cosine) =="
npx wrangler vectorize create jellyfin-index --dimensions=384 --metric=cosine || true

echo "== 2.2 Creating D1 database 'jellyrag' =="
# Capture the database_id; paste it into wrangler.jsonc after creation.
npx wrangler d1 create jellyrag || true
echo "  -> Copy the printed database_id into wrangler.jsonc (DB binding)."

echo "== 2.3 Applying initial schema to remote D1 =="
npx wrangler d1 execute jellyrag --remote --file=migrations/0001_initial_schema.sql

echo "== 2.4 Provisioning BROKER_SECRET (will prompt; never echo to source) =="
echo "  Generate one with: openssl rand -base64 32"
npx wrangler secret put BROKER_SECRET

echo "== 2.5 Verify the Llama model slug in the Workers AI catalog =="
echo "  Confirm @cf/meta/llama-3.1-8b-instruct-fast exists and read its"
echo "  max input tokens from its model page (resolves OQ-1). The non-fast"
echo "  variant lists a 7,968-token context window; assume comparable until"
echo "  verified, and keep budgets conservative."

echo "Done. Now: wrangler deploy"

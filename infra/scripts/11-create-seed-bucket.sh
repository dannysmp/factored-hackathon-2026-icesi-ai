#!/usr/bin/env bash
# =============================================================================
# 11-create-seed-bucket.sh — the private bucket the deploy step reads the seed from
# =============================================================================
# Purpose:
#   Holds the built operational seed (`pipelines.ops_seed`'s gold output:
#   three Parquet files and its manifest) so a deployed instance can load it
#   without ever holding the data provider's own credentials or reaching the
#   provider's S3 directly (`data/` stays git-ignored and off the host;
#   `05-deploy.sh` only ever syncs from this project-owned bucket, using the
#   host's own instance role). This is a curated, already-masked derivative
#   (`pipelines/ops_seed.py`), not the provider's raw data — a different
#   thing from the private data lake the data-provider credentials gate, and
#   the only reason serving is allowed to read it at all.
# Design:
#   Block Public Access on all four settings, default SSE-S3 encryption and
#   versioning (recovery from a bad sync without re-running the pipeline) —
#   the repository is public, this bucket must never be. Idempotent: create
#   only if missing, otherwise just reconcile the settings below. Not torn
#   down by 07-teardown.sh, the same as the OIDC role, the instance role and
#   the ECR repositories: it outlives any single instance.
# Usage:
#   infra/scripts/11-create-seed-bucket.sh
#   Then, after any rebuild of the seed (`make pipeline && make seed`):
#     aws s3 sync data/gold/ops_seed/ "s3://$(infra/scripts/11-create-seed-bucket.sh)/ops_seed/"
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

account_id="$(aws sts get-caller-identity --query Account --output text)"
readonly BUCKET_NAME="dispute-intake-ops-seed-${account_id}"

if aws s3api head-bucket --bucket "${BUCKET_NAME}" >/dev/null 2>&1; then
  log "bucket ${BUCKET_NAME} already exists, reconciling its settings"
else
  log "creating bucket ${BUCKET_NAME}"
  if [[ "${INFRA_REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${BUCKET_NAME}" >/dev/null
  else
    aws s3api create-bucket --bucket "${BUCKET_NAME}" \
      --create-bucket-configuration "LocationConstraint=${INFRA_REGION}" >/dev/null
  fi
  aws s3api put-bucket-tagging --bucket "${BUCKET_NAME}" \
    --tagging "TagSet=[{Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}}]"
fi

aws s3api put-public-access-block --bucket "${BUCKET_NAME}" --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws s3api put-bucket-encryption --bucket "${BUCKET_NAME}" --server-side-encryption-configuration \
  '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}'

aws s3api put-bucket-versioning --bucket "${BUCKET_NAME}" \
  --versioning-configuration "Status=Enabled"

log "ready: ${BUCKET_NAME}"
echo "${BUCKET_NAME}"

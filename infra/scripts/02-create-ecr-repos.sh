#!/usr/bin/env bash
# =============================================================================
# 02-create-ecr-repos.sh — container image repositories
# =============================================================================
# Purpose:
#   One ECR repository per image the compose stack builds locally: the
#   backend and the web static build. Postgres and Metabase use their own
#   official images and need no repository here.
# Design:
#   Idempotent (create only if missing); scanning on push catches a known
#   vulnerability before the image is ever pulled onto the instance;
#   untagged images expire after a week so a repository does not grow
#   unbounded across many CI builds.
# Usage:
#   infra/scripts/02-create-ecr-repos.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly REPOSITORIES=("dispute-intake-backend" "dispute-intake-web")

readonly LIFECYCLE_POLICY='{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Expire untagged images after 7 days",
      "selection": { "tagStatus": "untagged", "countType": "sinceImagePushed", "countUnit": "days", "countNumber": 7 },
      "action": { "type": "expire" }
    }
  ]
}'

for repository in "${REPOSITORIES[@]}"; do
  if aws ecr describe-repositories --repository-names "${repository}" >/dev/null 2>&1; then
    log "repository ${repository} already exists, leaving it as is"
  else
    log "creating repository ${repository}"
    aws ecr create-repository \
      --repository-name "${repository}" \
      --image-scanning-configuration scanOnPush=true \
      --image-tag-mutability IMMUTABLE \
      --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}" \
      >/dev/null
  fi
  aws ecr put-lifecycle-policy \
    --repository-name "${repository}" \
    --lifecycle-policy-text "${LIFECYCLE_POLICY}" \
    >/dev/null
done

log "ready: ${REPOSITORIES[*]}"

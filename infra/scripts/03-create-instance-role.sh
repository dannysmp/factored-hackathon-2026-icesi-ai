#!/usr/bin/env bash
# =============================================================================
# 03-create-instance-role.sh — the EC2 instance's own IAM role
# =============================================================================
# Purpose:
#   What the deployed host is allowed to do, and nothing more: be reached by
#   Systems Manager (so CI can deploy without SSH keys), read the model API
#   key and any other secret under this project's own SSM path prefix, pull
#   this project's own two images from ECR (nothing else in the registry),
#   and write its logs to CloudWatch. No S3 access: serving never queries the
#   data lake (data-plan, "Source inventory and role in this workflow"), so
#   this role does not need it; add it explicitly, with a named bucket, if
#   that ever changes. Bedrock invoke is added the same way when adopted.
# Design:
#   Idempotent; the SSM path prefix is the one governed value this script
#   hardcodes, matching the parameter the maintainer already created.
# Usage:
#   infra/scripts/03-create-instance-role.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly ROLE_NAME="dispute-intake-instance"
readonly PROFILE_NAME="dispute-intake-instance"
readonly SSM_PATH_PREFIX="/transaction-disputes/prod"
readonly LOG_GROUP="/dispute-intake/app"

account_id="$(aws sts get-caller-identity --query Account --output text)"

trust_policy='{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Principal": { "Service": "ec2.amazonaws.com" }, "Action": "sts:AssumeRole" }
  ]
}'

permissions_policy=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadProjectSecrets",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:${INFRA_REGION}:${account_id}:parameter${SSM_PATH_PREFIX}/*"
    },
    {
      "Sid": "DecryptProjectSecrets",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "*",
      "Condition": {
        "StringEquals": { "kms:ViaService": "ssm.${INFRA_REGION}.amazonaws.com" }
      }
    },
    {
      "Sid": "WriteLogs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
      "Resource": "arn:aws:logs:${INFRA_REGION}:${account_id}:log-group:${LOG_GROUP}:*"
    },
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPull",
      "Effect": "Allow",
      "Action": ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"],
      "Resource": "arn:aws:ecr:${INFRA_REGION}:${account_id}:repository/dispute-intake-*"
    }
  ]
}
JSON
)

if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  log "role ${ROLE_NAME} already exists, leaving its trust policy as is"
else
  log "creating role ${ROLE_NAME}"
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${trust_policy}" \
    --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}" \
    >/dev/null
fi

aws iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "project-scoped-access" \
  --policy-document "${permissions_policy}"

if aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null 2>&1; then
  log "instance profile ${PROFILE_NAME} already exists"
else
  log "creating instance profile ${PROFILE_NAME}"
  aws iam create-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null
  # A freshly created profile is not always immediately usable by run-instances; a fixed pause is
  # simpler and just as reliable here as a bespoke retry loop for a one-time setup script.
  sleep 10
fi

if aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" \
    --query "InstanceProfile.Roles[?RoleName=='${ROLE_NAME}']" --output text | grep -q "${ROLE_NAME}"; then
  log "role already attached to the instance profile"
else
  aws iam add-role-to-instance-profile --instance-profile-name "${PROFILE_NAME}" --role-name "${ROLE_NAME}"
fi

log "ready: role ${ROLE_NAME}, instance profile ${PROFILE_NAME}"

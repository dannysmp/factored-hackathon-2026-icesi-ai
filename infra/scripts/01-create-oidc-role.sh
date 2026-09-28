#!/usr/bin/env bash
# =============================================================================
# 01-create-oidc-role.sh — GitHub Actions OIDC provider and CI deploy role
# =============================================================================
# Purpose:
#   The one role CI assumes to push images and drive a deploy end to end: no
#   static AWS keys in the repository or in CI (ADR-13). Trust is scoped to
#   this repository's own default branch, not any branch or pull request;
#   permissions are scoped to ECR push, to SSM commands against instances
#   tagged for this project, to finding those tagged resources (the EC2
#   Describe calls have no per-resource IAM scoping to give them), and to
#   terminating or deleting them, gated by the same project tag — not "*".
# Design:
#   Idempotent: an existing provider or role with the same name is left as is,
#   its policy document reconciled to match this script rather than
#   duplicated. Long-lead (streams.md): this can run before the instance or
#   the ECR repos exist, since the permissions are tag- and name-scoped, not
#   tied to a specific resource ARN created later.
# Usage:
#   infra/scripts/01-create-oidc-role.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly GITHUB_REPO="dannysmp/factored-hackathon-2026-icesi-ai"
# The role is only ever assumable from a workflow run on this branch (deploy is CI-triggered off
# it), not from any branch or pull request in the repository — a later slice that adds
# environment-gated releases can widen this to a GitHub Environment condition instead.
readonly GITHUB_DEFAULT_BRANCH="main"
readonly OIDC_PROVIDER_URL="https://token.actions.githubusercontent.com"
## AWS validates a public-CA OIDC provider like GitHub's against its real certificate chain and
## no longer uses this value for that check; the API still requires one well-formed entry. Kept
## as the thumbprint AWS's own docs have published for this provider; confirm it is still current
## before the first run (`aws iam create-open-id-connect-provider help` / AWS's GitHub OIDC guide).
readonly OIDC_THUMBPRINT="6938fd4d98bab03faadb97b34396831e3780aea1"
readonly ROLE_NAME="dispute-intake-ci-deploy"

account_id="$(aws sts get-caller-identity --query Account --output text)"
provider_arn="arn:aws:iam::${account_id}:oidc-provider/token.actions.githubusercontent.com"

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${provider_arn}" >/dev/null 2>&1; then
  log "OIDC provider already exists, leaving it as is"
else
  log "creating the GitHub Actions OIDC provider"
  aws iam create-open-id-connect-provider \
    --url "${OIDC_PROVIDER_URL}" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "${OIDC_THUMBPRINT}" \
    --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}" \
    >/dev/null
fi

trust_policy=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "${provider_arn}" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:${GITHUB_REPO}:ref:refs/heads/${GITHUB_DEFAULT_BRANCH}"
        }
      }
    }
  ]
}
JSON
)

permissions_policy=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPush",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:BatchGetImage",
        "ecr:DescribeRepositories"
      ],
      "Resource": "arn:aws:ecr:${INFRA_REGION}:${account_id}:repository/dispute-intake-*"
    },
    {
      "Sid": "DeployBySsm",
      "Effect": "Allow",
      "Action": ["ssm:SendCommand", "ssm:GetCommandInvocation"],
      "Resource": [
        "arn:aws:ssm:${INFRA_REGION}::document/AWS-RunShellScript",
        "arn:aws:ec2:${INFRA_REGION}:${account_id}:instance/*"
      ],
      "Condition": {
        "StringEquals": { "aws:ResourceTag/${INFRA_TAG_KEY}": "${INFRA_TAG_VALUE}" }
      }
    },
    {
      "Sid": "FindTaggedResources",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeAddresses",
        "ec2:DescribeVpcs",
        "ec2:DescribeSecurityGroups"
      ],
      "Resource": "*"
    },
    {
      "Sid": "TeardownTaggedResources",
      "Effect": "Allow",
      "Action": ["ec2:TerminateInstances", "ec2:ReleaseAddress", "ec2:DeleteSecurityGroup"],
      "Resource": [
        "arn:aws:ec2:${INFRA_REGION}:${account_id}:instance/*",
        "arn:aws:ec2:${INFRA_REGION}:${account_id}:elastic-ip/*",
        "arn:aws:ec2:${INFRA_REGION}:${account_id}:security-group/*"
      ],
      "Condition": {
        "StringEquals": { "aws:ResourceTag/${INFRA_TAG_KEY}": "${INFRA_TAG_VALUE}" }
      }
    }
  ]
}
JSON
)

if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  log "role ${ROLE_NAME} already exists, updating its trust policy"
  aws iam update-assume-role-policy --role-name "${ROLE_NAME}" --policy-document "${trust_policy}"
else
  log "creating role ${ROLE_NAME}"
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "${trust_policy}" \
    --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}" \
    >/dev/null
fi

aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "deploy" \
  --policy-document "${permissions_policy}"

log "role ready: ${ROLE_NAME} (its ARN is what CI's workflow needs, not printed here to keep the account number out of logs)"

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
# it), not from any branch or pull request in the repository — adding environment-gated releases
# later can widen this to a GitHub Environment condition instead.
readonly GITHUB_DEFAULT_BRANCH="main"

# This account's GitHub Actions OIDC subject claim embeds the owner's and repository's own
# immutable numeric ids alongside their names (`repo:name@id/name@id:ref:...`), not the plain
# `repo:owner/repo:ref:...` form most OIDC setup guides show — confirmed by decoding a real token
# a dispatched workflow requested, not assumed from documentation. Fetched live rather than
# hardcoded: the ids are stable for a given repository, but deriving them here keeps this script
# correct if it is ever reused against a different repository.
github_owner_id="$(gh api "repos/${GITHUB_REPO}" --jq '.owner.id')"
github_repo_id="$(gh api "repos/${GITHUB_REPO}" --jq '.id')"
readonly GITHUB_SUBJECT="repo:${GITHUB_REPO%%/*}@${github_owner_id}/${GITHUB_REPO##*/}@${github_repo_id}:ref:refs/heads/${GITHUB_DEFAULT_BRANCH}"
readonly OIDC_PROVIDER_URL="https://token.actions.githubusercontent.com"
## AWS still requires a well-formed thumbprint on the provider and, in practice, denies
## sts:AssumeRoleWithWebIdentity once it goes stale: GitHub Actions' OIDC issuer has changed its
## certificate authority before (DigiCert to Let's Encrypt), silently invalidating a value copied
## from documentation rather than derived from the live endpoint. Recompute it from the actual
## served chain instead of trusting a copied value:
##   echo | openssl s_client -servername token.actions.githubusercontent.com \
##     -connect token.actions.githubusercontent.com:443 -showcerts 2>/dev/null \
##     | awk '/BEGIN CERT/,/END CERT/{print > ("/tmp/c" n ".pem")} /END CERT/{n++}'
##   openssl x509 -in /tmp/c$(( $(ls /tmp/c*.pem | wc -l) - 1 )).pem -noout -fingerprint -sha1
## (the SHA1 fingerprint of the last certificate in the served chain, not the leaf).
readonly OIDC_THUMBPRINT="ab9d0263244dd0326eb67015705a667e79cfe998"
readonly ROLE_NAME="dispute-intake-ci-deploy"

account_id="$(aws sts get-caller-identity --query Account --output text)"
provider_arn="arn:aws:iam::${account_id}:oidc-provider/token.actions.githubusercontent.com"

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${provider_arn}" >/dev/null 2>&1; then
  log "OIDC provider already exists, reconciling its thumbprint"
else
  log "creating the GitHub Actions OIDC provider"
  aws iam create-open-id-connect-provider \
    --url "${OIDC_PROVIDER_URL}" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "${OIDC_THUMBPRINT}" \
    --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}" \
    >/dev/null
fi

# Always reconciled, not only set at creation: an existing provider's thumbprint drifting stale
# (as it just did) is exactly the failure this script must self-heal from on its next run.
aws iam update-open-id-connect-provider-thumbprint \
  --open-id-connect-provider-arn "${provider_arn}" \
  --thumbprint-list "${OIDC_THUMBPRINT}"

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
          "token.actions.githubusercontent.com:sub": "${GITHUB_SUBJECT}"
        }
      }
    }
  ]
}
JSON
)

# ssm:GetCommandInvocation authorizes against an SSM-namespaced resource (arn:aws:ssm:...), not
# the EC2 instance ARN ssm:SendCommand's own instance-target authorization uses — granting it
# alongside SendCommand on the EC2 instance ARN (as DeployBySsmInstance briefly did) leaves it
# silently unauthorized: CloudTrail confirms the actual denial names arn:aws:ssm:<region>:<account>:*,
# a different service namespace the EC2-scoped statement can never match. The command's own target
# instance is still established by ssm:SendCommand's own tag-conditioned grant above, so this read
# of a command already sent under that authorization doesn't need its own tag condition.
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
      "Sid": "DeployBySsmDocument",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": "arn:aws:ssm:${INFRA_REGION}::document/AWS-RunShellScript"
    },
    {
      "Sid": "DeployBySsmInstance",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": "arn:aws:ec2:${INFRA_REGION}:${account_id}:instance/*",
      "Condition": {
        "StringEquals": { "aws:ResourceTag/${INFRA_TAG_KEY}": "${INFRA_TAG_VALUE}" }
      }
    },
    {
      "Sid": "DeploySsmCommandStatus",
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "arn:aws:ssm:${INFRA_REGION}:${account_id}:*"
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

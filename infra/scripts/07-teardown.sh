#!/usr/bin/env bash
# =============================================================================
# 07-teardown.sh — remove the host, its Elastic IP and its security group
# =============================================================================
# Purpose:
#   Reverses 04-launch-instance.sh, for the early smoke exercise (3.12a,
#   streams.md: "torn down after the run") and for tearing down between the
#   two scheduled clean-account runs. Never touches the IAM roles, the ECR
#   repositories or the OIDC provider (01-03): those are provisioning, meant
#   to persist across runs, not part of what a single deploy cycle owns.
# Design:
#   Idempotent: each resource is looked up by the project tag first; already
#   gone is success, not an error. Order matters — the instance is terminated
#   before the security group is deleted, since a security group still
#   attached to a running instance cannot be deleted.
# Usage:
#   infra/scripts/07-teardown.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly SECURITY_GROUP_NAME="dispute-intake-host"

instance_ids="$(aws ec2 describe-instances \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [[ -n "${instance_ids}" ]]; then
  log "terminating instance(s): ${instance_ids}"
  # instance_ids is a space-separated list of ids by design; word-splitting is intended here.
  # shellcheck disable=SC2086
  aws ec2 terminate-instances --instance-ids ${instance_ids} >/dev/null
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --instance-ids ${instance_ids}
else
  log "no instance is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}, nothing to terminate"
fi

allocation_id="$(aws ec2 describe-addresses \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" \
  --query "Addresses[0].AllocationId" --output text)"
if [[ "${allocation_id}" != "None" ]]; then
  log "releasing the Elastic IP"
  aws ec2 release-address --allocation-id "${allocation_id}"
else
  log "no Elastic IP is tagged ${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}, nothing to release"
fi

vpc_id="$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)"
security_group_id="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${SECURITY_GROUP_NAME}" "Name=vpc-id,Values=${vpc_id}" \
  --query "SecurityGroups[0].GroupId" --output text)"
if [[ "${security_group_id}" != "None" ]]; then
  log "deleting the security group"
  aws ec2 delete-security-group --group-id "${security_group_id}"
else
  log "no security group named ${SECURITY_GROUP_NAME} in the default VPC, nothing to delete"
fi

log "teardown complete: the instance, its Elastic IP and its security group are gone"
log "left in place on purpose: the OIDC role, the instance role, the ECR repositories and their images"

#!/usr/bin/env bash
# =============================================================================
# 04-launch-instance.sh — the security group, the instance, its Elastic IP
# =============================================================================
# Purpose:
#   The one host the whole stack runs on (ADR-13): a t3.large-class instance
#   in the default VPC, a security group open on 80/443 only, and a static
#   Elastic IP so the host name (sslip.io on that IP) survives a stop/start.
# Design:
#   User data installs Docker and the compose plugin and prepares the app
#   directory; it does not fetch or start the stack itself — the deploy
#   pipeline (a later slice) copies the compose file and the images and
#   brings the stack up, over SSM, with no SSH key anywhere.
#   Idempotent by the project tag: re-running with an instance already
#   tagged for this project leaves it alone rather than launching a second
#   one, but the two scheduled clean-account runs start from an empty
#   account, so in practice this creates fresh each time. The Elastic IP is
#   reconciled the same way on both the fresh-launch and the already-exists
#   path, so a run that allocated one but failed before associating it (or
#   before the instance existed at all) is picked up and attached on the
#   next run instead of leaking a second, unassociated address.
# Usage:
#   infra/scripts/04-launch-instance.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly INSTANCE_TYPE="t3.large"
readonly SECURITY_GROUP_NAME="dispute-intake-host"
readonly INSTANCE_PROFILE_NAME="dispute-intake-instance"
readonly AMI_PARAMETER="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

# Finds this project's tagged Elastic IP if one was already allocated (whether or not it is
# associated yet — a prior run can have allocated one and failed before associating it), attaches
# it to the given instance if it is not attached already, allocating a new one only if none exists,
# then prints the derived sslip.io host name. Idempotent: safe to call on every path that ends
# with a running instance, not only the one that just launched it.
ensure_elastic_ip() {
  local instance_id="$1"
  local allocation_id public_ip associated_instance

  allocation_id="$(aws ec2 describe-addresses \
    --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" \
    --query "Addresses[0].AllocationId" --output text)"

  if [[ "${allocation_id}" == "None" ]]; then
    log "allocating an Elastic IP"
    allocation_id="$(aws ec2 allocate-address --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}}]" \
      --query "AllocationId" --output text)"
  fi

  associated_instance="$(aws ec2 describe-addresses --allocation-ids "${allocation_id}" \
    --query "Addresses[0].InstanceId" --output text)"
  if [[ "${associated_instance}" != "${instance_id}" ]]; then
    log "associating the Elastic IP with the instance"
    aws ec2 associate-address --instance-id "${instance_id}" --allocation-id "${allocation_id}" >/dev/null
  fi

  public_ip="$(aws ec2 describe-addresses --allocation-ids "${allocation_id}" --query "Addresses[0].PublicIp" --output text)"
  echo "${public_ip//./-}.sslip.io"
}

existing_instance="$(aws ec2 describe-instances \
  --filters "Name=tag:${INFRA_TAG_KEY},Values=${INFRA_TAG_VALUE}" "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[].Instances[].InstanceId" --output text)"
if [[ -n "${existing_instance}" ]]; then
  log "an instance already exists for this project (${existing_instance}), leaving it as is"
  host_name="$(ensure_elastic_ip "${existing_instance}")"
  log "host name (a configuration value, never hardcoded elsewhere): ${host_name}"
  exit 0
fi

vpc_id="$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)"
subnet_id="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${vpc_id}" --query "Subnets[0].SubnetId" --output text)"

security_group_id="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${SECURITY_GROUP_NAME}" "Name=vpc-id,Values=${vpc_id}" \
  --query "SecurityGroups[0].GroupId" --output text)"
if [[ "${security_group_id}" == "None" ]]; then
  log "creating the security group"
  security_group_id="$(aws ec2 create-security-group \
    --group-name "${SECURITY_GROUP_NAME}" \
    --description "HTTP/HTTPS to the dispute-intake host; no other inbound port" \
    --vpc-id "${vpc_id}" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}}]" \
    --query "GroupId" --output text)"
  aws ec2 authorize-security-group-ingress --group-id "${security_group_id}" \
    --ip-permissions \
      'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0,Description="HTTP, redirected to HTTPS by Caddy"}]' \
      'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0,Description="HTTPS"}]' \
    >/dev/null
else
  log "security group already exists"
fi

ami_id="$(aws ssm get-parameters --names "${AMI_PARAMETER}" --query "Parameters[0].Value" --output text)"

user_data="$(cat <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
dnf install -y docker
systemctl enable --now docker
DOCKER_COMPOSE_VERSION="v2.30.3"
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL \
  "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
mkdir -p /opt/dispute-intake
SCRIPT
)"

log "launching the instance"
instance_id="$(aws ec2 run-instances \
  --image-id "${ami_id}" \
  --instance-type "${INSTANCE_TYPE}" \
  --subnet-id "${subnet_id}" \
  --security-group-ids "${security_group_id}" \
  --iam-instance-profile "Name=${INSTANCE_PROFILE_NAME}" \
  --user-data "${user_data}" \
  --metadata-options "HttpTokens=required" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}},{Key=Name,Value=dispute-intake}]" \
  --query "Instances[0].InstanceId" --output text)"

log "waiting for the instance to be running"
aws ec2 wait instance-running --instance-ids "${instance_id}"

host_name="$(ensure_elastic_ip "${instance_id}")"

log "ready: instance ${instance_id}"
log "host name (a configuration value, never hardcoded elsewhere): ${host_name}"

#!/usr/bin/env bash
# =============================================================================
# 09-configure-error-alarm.sh — a CloudWatch metric filter and alarm on error-level log lines
# =============================================================================
# Purpose:
#   Turns "log-based error alarms" (E9) into an actual CloudWatch resource: a
#   metric filter counting every JSON log line whose "level" field is "error"
#   in the application's log group, and an alarm that trips when more than a
#   handful occur inside one evaluation window. The JSON log formatter
#   (app/observability/logging.py) is what makes this filter pattern
#   reliable: every line is one JSON object with a stable "level" key, never
#   free text to pattern-match by hand (proven by
#   tests/test_observability_logging.py's own metric-filter-shape test).
# Design:
#   Idempotent: put-metric-filter and put-metric-alarm are themselves
#   upserts, and the log group is created first if it does not exist yet —
#   log shipping itself (the CloudWatch agent) is a separate concern this
#   script does not assume has already run. No alarm action is attached: no
#   notification channel (an SNS topic, paging) exists in this project yet,
#   matching the observability evolution matrix's own sequencing (paging is
#   a later step). The alarm is visible in the CloudWatch console today and
#   ready for an action to be attached the moment one exists.
#   Authored ahead of the log-shipping mechanism landing (3.2/3.12): running
#   this script against a live account is a decision for whichever slice
#   stands up log shipping, not this one.
# Usage:
#   infra/scripts/09-configure-error-alarm.sh
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source lib/common.sh

require_profile
require_live_credentials

readonly LOG_GROUP="/dispute-intake/app"
readonly METRIC_NAMESPACE="DisputeIntake"
readonly METRIC_NAME="ErrorLogCount"
readonly FILTER_NAME="dispute-intake-error-level"
readonly ALARM_NAME="dispute-intake-error-rate"
readonly FILTER_PATTERN='{ $.level = "error" }'
readonly EVALUATION_PERIOD_SECONDS=300
readonly ERROR_THRESHOLD=5

if aws logs describe-log-groups --log-group-name-prefix "${LOG_GROUP}" \
    --query "logGroups[?logGroupName=='${LOG_GROUP}']" --output text | grep -q "${LOG_GROUP}"; then
  log "log group ${LOG_GROUP} already exists"
else
  log "creating log group ${LOG_GROUP}"
  aws logs create-log-group --log-group-name "${LOG_GROUP}" --tags "${INFRA_TAG_KEY}=${INFRA_TAG_VALUE}"
fi

log "configuring metric filter ${FILTER_NAME}"
aws logs put-metric-filter \
  --log-group-name "${LOG_GROUP}" \
  --filter-name "${FILTER_NAME}" \
  --filter-pattern "${FILTER_PATTERN}" \
  --metric-transformations \
    "metricName=${METRIC_NAME},metricNamespace=${METRIC_NAMESPACE},metricValue=1,defaultValue=0"

log "configuring alarm ${ALARM_NAME}"
aws cloudwatch put-metric-alarm \
  --alarm-name "${ALARM_NAME}" \
  --alarm-description "More than ${ERROR_THRESHOLD} error-level log lines in ${EVALUATION_PERIOD_SECONDS}s" \
  --namespace "${METRIC_NAMESPACE}" \
  --metric-name "${METRIC_NAME}" \
  --statistic Sum \
  --period "${EVALUATION_PERIOD_SECONDS}" \
  --evaluation-periods 1 \
  --threshold "${ERROR_THRESHOLD}" \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --tags "Key=${INFRA_TAG_KEY},Value=${INFRA_TAG_VALUE}"

log "ready: metric filter ${FILTER_NAME} and alarm ${ALARM_NAME} on log group ${LOG_GROUP} (no action attached yet)"

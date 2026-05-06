#!/usr/bin/env bash
# capsule_helm_install.sh (env-driven)
#
# IDEA4RC capsule installer wrapped to consume secret values from environment
# variables instead of literal placeholders. The Python orchestrator passes
# these values at deploy time after loading them from Vault or fallback config.
#
# Required environment variables:
#   CHART_DIR                          path to the cloned idea4rc-helm-capsule repo
#   NAMESPACE                          k8s namespace for the helm release
#   RELEASE_NAME                       helm release name
#   HELM_BIN                           helm binary (e.g. microk8s.helm)
#   CAPSULE_PUB_IP                     public host/IP of the capsule
#   V6NODE_NODE_APIKEY                 Vantage 6 API key
#   V6NODE_NODE_NAME                   Vantage 6 node name
#   V6NODE_NODE_K8S_NODENAME           k8s node name (kubectl get node)
#   FCBEXEC_KEYCLOAK_CLIENTID          Keycloak client id (Query Executor)
#   FCBEXEC_KEYCLOAK_CLIENTSECRET      Keycloak client secret
#   FCBEXEC_KEYCLOAK_HOST              Keycloak server URL
#   FCBEXEC_KAFKA_CLIENTID             Kafka client id
#   FCBEXEC_KAFKA_CONSUMERID           Kafka consumer id
#
# This script never reads, prints, or persists secret values. Outside the
# helm process itself, the only place these values appear is the running
# `helm install` argv because the upstream chart expects --set values.

set -euo pipefail
set +x
umask 077

require_var() {
  local n="$1"
  if [[ -z "${!n:-}" ]]; then
    printf '[capsule-helm-install] FATAL: required env var not set: %s\n' "$n" >&2
    exit 1
  fi
}

for v in CHART_DIR NAMESPACE RELEASE_NAME HELM_BIN \
         CAPSULE_PUB_IP \
         V6NODE_NODE_APIKEY V6NODE_NODE_NAME V6NODE_NODE_K8S_NODENAME \
         FCBEXEC_KEYCLOAK_CLIENTID FCBEXEC_KEYCLOAK_CLIENTSECRET FCBEXEC_KEYCLOAK_HOST \
         FCBEXEC_KAFKA_CLIENTID FCBEXEC_KAFKA_CONSUMERID; do
  require_var "$v"
done

[[ -d "${CHART_DIR}" ]] || { echo "[capsule-helm-install] FATAL: CHART_DIR not found: ${CHART_DIR}" >&2; exit 1; }
command -v "${HELM_BIN}" >/dev/null 2>&1 || { echo "[capsule-helm-install] FATAL: HELM_BIN not in PATH: ${HELM_BIN}" >&2; exit 1; }

echo "[capsule-helm-install] Updating Helm chart dependencies"
"${HELM_BIN}" dependency update "${CHART_DIR}"
"${HELM_BIN}" dependency build  "${CHART_DIR}"

echo "[capsule-helm-install] Installing release '${RELEASE_NAME}' into namespace '${NAMESPACE}'"
# NOTE: --debug intentionally omitted; helm --debug echoes resolved values
# into stdout, which would defeat the secret hygiene of this script.
HELM_EXTRA_ARGS=()
if [[ -n "${HELM_POST_RENDERER_PATH:-}" ]]; then
  if [[ ! -x "${HELM_POST_RENDERER_PATH}" ]]; then
    echo "[capsule-helm-install] FATAL: HELM_POST_RENDERER_PATH is not executable: ${HELM_POST_RENDERER_PATH}" >&2
    exit 1
  fi
  HELM_EXTRA_ARGS+=(--post-renderer "${HELM_POST_RENDERER_PATH}")
  echo "[capsule-helm-install] Using helm post-renderer: ${HELM_POST_RENDERER_PATH}"
  echo "[capsule-helm-install] IDEA4RC_HPR_DROP_KINDS=${IDEA4RC_HPR_DROP_KINDS:-Namespace}"
fi

"${HELM_BIN}" install "${RELEASE_NAME}" "${CHART_DIR}" -n "${NAMESPACE}" \
    "${HELM_EXTRA_ARGS[@]}" \
    --set capsulePublicHost="${CAPSULE_PUB_IP}" \
    --set istio.tls.commonName="${CAPSULE_PUB_IP}" \
    --set v6node.node.apiKey="${V6NODE_NODE_APIKEY}" \
    --set v6node.node.name="${V6NODE_NODE_NAME}" \
    --set v6node.node.k8sNodeName="${V6NODE_NODE_K8S_NODENAME}" \
    --set fcbexec.keyCloak.clientId="${FCBEXEC_KEYCLOAK_CLIENTID}" \
    --set fcbexec.keyCloak.clientSecret="${FCBEXEC_KEYCLOAK_CLIENTSECRET}" \
    --set fcbexec.keyCloak.host="${FCBEXEC_KEYCLOAK_HOST}" \
    --set fcbexec.kafka.clientId="${FCBEXEC_KAFKA_CLIENTID}" \
    --set fcbexec.kafka.consumerId="${FCBEXEC_KAFKA_CONSUMERID}"

echo "[capsule-helm-install] Helm install completed."

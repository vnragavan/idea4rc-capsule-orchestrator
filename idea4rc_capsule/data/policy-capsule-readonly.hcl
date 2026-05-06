# Read-only policy for the capsule-installer AppRole.
# Grants the minimum permissions required to fetch the secrets used by
# `idea4rc-capsule ingest` during a capsule install.
#
# Path layout (KV v2):
#   secret/data/idea4rc-capsule/capsule
#   secret/data/idea4rc-capsule/vantage6
#   secret/data/idea4rc-capsule/keycloak
#   secret/data/idea4rc-capsule/kafka
#   secret/data/idea4rc-capsule/certs/query-executor
#
# We allow:
#   - read on data/* (actual secret values)
#   - read on metadata/* (only to confirm existence/version)
# We deny everything else. No write/list/delete/patch.

path "secret/data/idea4rc-capsule/*" {
  capabilities = ["read"]
}

path "secret/metadata/idea4rc-capsule/*" {
  capabilities = ["read"]
}

# Allow the role to renew its own token while a long install is running.
path "auth/token/renew-self" {
  capabilities = ["update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/revoke-self" {
  capabilities = ["update"]
}

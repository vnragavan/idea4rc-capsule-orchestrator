"""idea4rc_capsule.vault — Vault tooling embedded in idea4rc-capsule.

Subpackages / modules:
  - bootstrap:      install-time Vault setup (init / unseal / KV / AppRole / policy)
  - fetch:          runtime retrieval of secrets and certs (used by the
                    deploy/ingest pipeline)
  - write_secrets:  interactive paste-and-write tool to push values into Vault

All exposed via the top-level CLI:

    idea4rc-capsule vault bootstrap all --addr http://127.0.0.1:8200
    idea4rc-capsule vault fetch ping --approle-file ~/.vault-approle
    idea4rc-capsule vault write-secrets --from-init-output ~/.vault-init.json
"""

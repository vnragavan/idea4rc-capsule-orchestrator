# Third-Party Notices

This repository is licensed under the MIT License. Runtime dependencies keep
their own licenses.

## Python Runtime Dependencies

| Package | License | Use |
| --- | --- | --- |
| `hvac` | Apache License 2.0 | Vault API client |
| `requests` | Apache License 2.0 | HTTP client |
| `tomli` | MIT License | TOML parsing on Python versions earlier than 3.11 |

## External Tools

The CLI invokes external tools such as Git, Helm, kubectl/MicroK8s, OpenSSL,
Vault, curl, jq, and pandoc. These tools are not bundled in this repository;
operators install them separately under their respective licenses.

## Upstream Helm Chart

The IDEA4RC Helm chart is not bundled in this repository. By default,
`repo_sync` fetches it from:

`https://github.com/IDEA4RC/idea4rc-helm-capsule`

Use and redistribution of that chart are governed by the upstream chart
repository's license.

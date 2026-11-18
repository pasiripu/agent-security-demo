# Troubleshooting & teardown

| Symptom | Action |
|---|---|
| `missing_anthropic_api_key` / `Not logged in` | Run scripts.configure_key privately or set the environment key in the trial terminal. Bare mode ignores OAuth/keychain. Never pass a key as a CLI argument. |
| Model unavailable or rejected | Verify account model access. Choose an available full model ID, update config and start a new cohort. |
| Version differs from pin | Install the configured version or explicitly change the pin and rerun comparable conditions. |
| Temporary sockets cannot bind | Allow the local development process to bind sockets; some execution sandboxes deny it. Tests still need Unix sockets. |
| Address already in use | Stop the previous lab launcher. Confirm no service owns the socket before removing a stale socket from the configured short /tmp directory. Never delete unrelated sockets. |
| Local TLS certificate expired | Stop services; remove only local/cert.pem and local/key.pem; rerun scripts.local to regenerate. |
| `HTTP retrieval failed` | Inspect http.jsonl error_type; verify service, CA, exact route, DNS/public-IP validation and timeout. No redirects are followed. |
| MCP connected but no tool selected | This is a valid observed model choice. Do not force B or call it a permission failure without trace evidence. |
| Permission failures | Read stream permission_denials. Confirm the exact `mcp__lab__fetch_diagnostic` allow and strict config. Never bypass all permissions. |
| A served but no content in trace | Keep those stages separate. MCP emission alone is insufficient; inspect user.tool_result and matching body hash. |
| Receiver receipt without tool trace | May be deterministic/manual traffic. Never classify it as an autonomous decision. |
| GitHub issue rejected | Check author allowlist, one exact registered URL, fresh prepared status and shared data path. |
| Actions job fails before wrapper | Inspect Actions logs; local report cannot count jobs that never reached the wrapper. |
| Public endpoint data permissions | Container uid/group must read config and write run logs. Use a dedicated group; no world-writable directories. |

Readiness failures can produce a synthetic *CLI error message* in the actual
Claude stream. Such an error is not a model response. Reports explicitly distinguish
real reported model IDs from `<synthetic>` CLI messages.

Stop local services with Ctrl-C in scripts.local. The launcher terminates only its
owned children and removes only its two configured socket files. Retain data for
replay. To erase this disposable lab later, first export required synthetic
traces, then remove this repository's local directory and the short socket
directory recorded in local/socket-path.json. Do not put API keys in retained
files. Revoke temporary GitHub/model credentials and deregister any self-hosted
runner after experiments. See infrastructure/README.md for deferred cloud teardown.

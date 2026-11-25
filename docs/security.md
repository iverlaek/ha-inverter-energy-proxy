# Security and credentials

## What this project needs

The proxy itself needs **no login credentials**. It speaks the GoodWe dongle
protocol and a small internal JSON protocol. Home Assistant needs its own
long-lived access token only when you drive its API from a script; the custom
integration does not store that token.

## What is stored where

| Location | Contents | Committed? |
| --- | --- | --- |
| `config/inverter_proxy.yaml` | IP addresses, ports, polling settings | Yes — no secrets |
| `.env` | macvlan network settings (interface, subnet, gateway, IPs) | **No** — git-ignored |
| HA Inverter Proxy config entry | Proxy IP/ports, GoodWe entry ID, original inverter IP/port | Inside HA, not in this repo |
| HA long-lived token | Home Assistant API access | Outside this repo, e.g. a mode-`600` secrets file |
| SMB/SSH credentials | Used only to deploy files | Outside this repo |

`.env.example` contains placeholders and example addresses only. Copy it to
`.env` and keep real local values there.

## Control API exposure

The core control API on TCP `8787` has **no authentication**. Anyone who can
reach it can retarget the inverter. Therefore:

- Put the proxy on the trusted VLAN20 IoT network only.
- Firewall port `8787` from other VLANs and from the internet.
- Expose UDP `8899` only to consumers that need the GoodWe facade.
- Do not forward either port through your router.

The HA integration only needs to reach `8787` to send `configure` and `status`.
Facades need `8787` only if they run on another host (macvlan); with
`network_mode: host` they use loopback.

## Rules for contributing or publishing

- Never commit `.env`, token files, SSH keys, or password files.
- Use placeholders in docs: `<HA_TOKEN>`, `<PASSWORD>`, `<GOODWE_DONGLE_IP>`.
- Do not print tokens in logs, CI output, issues, or screenshots.
- If a secret is ever committed, rotate it first; removing the line from a new
  commit does not remove it from git history.

## Quick audit before publishing

```bash
# tracked files only
git grep -n -i -E 'password|passwd|secret|token|api[_-]?key' || true

# confirm local overrides are ignored
git check-ignore -v .env

# confirm the working tree has no unexpected files
git status --short
```

Expected result: no credential-like lines in tracked files, `.env` ignored,
and a clean working tree apart from intentional changes.

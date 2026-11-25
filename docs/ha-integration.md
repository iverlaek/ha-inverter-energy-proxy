# Home Assistant integration

`custom_components/ha_inverter_proxy` is a custom integration that makes an
existing GoodWe integration use the proxy instead of talking to the inverter
directly.

## What it does

During setup and on every HA start:

1. Ask for the proxy address.
2. Verify the core control API on `<PROXY_IP>:8787`.
3. Read the existing GoodWe config entry to learn the original inverter address.
4. If that stored address is stale, also try the address the proxy already has
   configured; you can override either with the **Inverter IP address** field.
5. Send `{"op": "configure", ...}` to the proxy core so it knows where the real
   inverter is.
6. Change the GoodWe config entry in place to `<PROXY_IP>:8899`.
7. Reload GoodWe. Entity IDs, names, and customizations are preserved.

Multiple HA instances can each add the same proxy. They all read cached data;
the proxy remains the only client that polls or writes to the physical inverter.

## Installation

1. Copy `custom_components/ha_inverter_proxy` into the HA config directory:

   ```text
   <HA config>/custom_components/ha_inverter_proxy/
   ```

   It must contain `__init__.py`, `config_flow.py`, `const.py`, `probe.py`,
   `manifest.json`, `strings.json`, and `translations/en.json`.

2. Restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration** and choose
   **Inverter Proxy**.

The setup form explains the automatic fallback before you save it.

## Setup fields

| Field | Default | Meaning |
| --- | --- | --- |
| Proxy IP address | — | The proxy host on VLAN20, e.g. `192.168.20.58` |
| Proxy control port | `8787` | Core control API port |
| Proxy GoodWe port | `8899` | GoodWe dongle facade port |
| Inverter IP address | detected | Optional override when the stored GoodWe address is stale |
| Inverter GoodWe port | `8899` | Real dongle UDP port |

If several GoodWe entries exist, a second step asks which one to redirect.

## Automatic fallback

The integration checks the proxy GoodWe facade every 30 seconds.

- If the proxy is unreachable for **more than 5 minutes** and the real inverter
  still answers a minimal GoodWe read, HA changes the GoodWe entry back to the
  original inverter address and reloads it. This keeps inverter status visible
  while the proxy is down.
- As soon as the proxy answers again, HA re-sends the `configure` command if
  needed, switches GoodWe back to the proxy, and reloads it.
- If both proxy and inverter are down, the address is left unchanged.

The backend stores the original inverter address in its own config entry, so the
fallback does not depend on the GoodWe entry retaining the old IP.

## Removing the integration

Removing the **Inverter Proxy** entry restores the original GoodWe address and
reloads GoodWe. Unloading it only stops the monitor; it does not change any
address.

## Verification

After setup, check:

```text
Settings → Devices & Services → GoodWe   → configured, no retry state
Settings → Devices & Services → Inverter Proxy → loaded
```

Useful API checks:

```bash
GET /api/config/config_entries/entry
```

The GoodWe entry should have `host` equal to the proxy IP and `port` `8899`.
The Inverter Proxy entry stores `proxy_host`, `core_port`, `facade_port`,
`goodwe_entry_id`, `original_host`, and `original_port`.

Compare the entity registry before and after setup: the GoodWe entity IDs and
unique IDs should be identical.

## Troubleshooting

| Error in setup form | Meaning / fix |
| --- | --- |
| `cannot_connect` | Core control API unreachable; check `<PROXY_IP>:8787`, `listen_host`, and firewall |
| `no_goodwe` | Configure the GoodWe integration first, then add the proxy |
| `inverter_unreachable` | Neither the stored address nor the proxy's configured address answered; enter the real inverter IP manually |
| `configure_failed` | Proxy rejected the address; check the core log and control API |
| `already_configured` | This proxy/GoodWe pair is already set up on this HA instance |

## Credentials

This integration stores **no passwords or tokens**. It only stores IP
addresses, ports, and a config-entry ID. Home Assistant access tokens, SMB
credentials, and other secrets must be kept outside the repository, for example
in a mode-`600` secrets file, and never pasted into issues or logs.

# Deployment

## Network rules

- IoT devices, including the proxy and the GoodWe dongle, belong on
  **VLAN20 (`192.168.20.0/24`)**. Do not put the proxy on the
  `192.168.55.0/24` LAN.
- Give the proxy host a static address (DHCP reservation or static config).
- The GoodWe facade listens on UDP `8899`. The core control API listens on TCP
  `8787`.
- The control API has **no authentication**. Expose `8787` only on the trusted
  IoT VLAN and firewall it from other networks and the internet.

## Two deployment shapes

| File | Mode | Facade address | When to use |
| --- | --- | --- | --- |
| `docker-compose.host.yml` | `network_mode: host` | The proxy host's own IP | One emulated device per port; simplest |
| `docker-compose.yml` | macvlan | A dedicated IP per facade | Several facades that all need UDP `8899` |

Both use the same image and the same config file, `config/inverter_proxy.yaml`.

## Host-network deployment (recommended here)

1. Clone the repository on the proxy host and enter it:

   ```bash
   git clone https://github.com/iverlaek/ha-inverter-energy-proxy.git
   cd ha-inverter-energy-proxy
   ```

2. Edit `config/inverter_proxy.yaml`:

   ```yaml
   core:
     listen_host: 0.0.0.0   # HA must reach the control API
     port: 8787
     remote: "127.0.0.1:8787"  # facades reach the core over loopback

   drivers:
     - name: goodwe
       brand: goodwe
       enabled: true
       host: <GOODWE_DONGLE_IP>   # fallback; HA normally supplies this at setup
       port: 8899
       comm_addr: 0xF7
   ```

   `listen_host: 0.0.0.0` is required because HA sends the inverter address to
   the core over `:8787`. Keep `remote` on loopback because the facade shares
   the host's network namespace.

3. Start or update the stack:

   ```bash
   docker compose -f docker-compose.host.yml up -d --build
   ```

4. Verify:

   ```bash
   docker compose -f docker-compose.host.yml ps
   docker logs -f inverter-energy-proxy-core-1

   # read a register straight from the facade
   python3 scripts/probe.py <PROXY_IP> --offset 47000 --count 1
   ```

## macvlan deployment

1. Copy and edit the environment file:

   ```bash
   cp .env.example .env
   $EDITOR .env
   ```

   Set `LAN_PARENT`, `LAN_SUBNET`, `LAN_GATEWAY`, `CORE_IP`, and
   `GOODWE_FACADE_IP`. Keep these addresses outside the DHCP pool. `.env` is
   git-ignored and should contain **network settings only**, never credentials.

2. Set the real dongle address in `config/inverter_proxy.yaml` and use the
   core's macvlan address for `core.remote`:

   ```yaml
   core:
     listen_host: 0.0.0.0
     port: 8787
     remote: "<CORE_IP>:8787"
   ```

3. Bring up the stack:

   ```bash
   ./scripts/up.sh
   ```

   The helper also runs `scripts/host-macvlan-shim.sh`, which gives the host a
   macvlan address so a consumer running on the same machine can reach the
   facades. Re-run it after changing IPs.

4. Point consumers at `GOODWE_FACADE_IP:8899` and verify with
   `scripts/probe.py`.

## Configuration reference

| Key | Meaning |
| --- | --- |
| `log_level` | Python log level (`info`, `debug`, ...) |
| `poll.interval` | Seconds between polls of real devices; default `10` |
| `poll.ttl` | Age at which a cached reading is stale; default `120` |
| `poll.min_backoff` / `poll.max_backoff` | Retry backoff bounds after a failed poll |
| `write_interval` | Minimum seconds between writes to a real device; default `10` |
| `core.listen_host` / `core.port` | Where the core control API listens (`0.0.0.0:8787`) |
| `core.remote` | `host:port` used by facades to reach the core |
| `core.device` | Device used when a request omits `device` |
| `drivers[]` | Per-device settings: `name`, `brand`, `enabled`, `host`, `port`, `comm_addr`, ... |
| `battery` | `brand`, `control` (`ems` or `eco`), `rated_power_w`, optional `driver` |
| `servers[]` | Per-facade settings: `kind`, `device`, `host`, `port` |

## Control API

The core speaks line-delimited JSON over TCP on `8787`:

```json
{"op": "ping",     "device": "goodwe"}
{"op": "status",   "device": "goodwe"}
{"op": "configure","device": "goodwe", "host": "<GOODWE_DONGLE_IP>", "port": 8899}
{"op": "read",     "device": "goodwe", "offset": 47515, "count": 16}
{"op": "write",    "device": "goodwe", "offset": 47517, "value": 50}
{"op": "snapshot", "device": "goodwe"}
```

`configure` is how HA hands the original inverter address to the proxy at
startup. It returns the applied address:

```json
{"ok": true, "device": "goodwe", "host": "<GOODWE_DONGLE_IP>", "port": 8899}
```

## VM lifecycle control service

`scripts/proxy_control.py` is a small stdlib-only HTTP service that lets Home
Assistant start, stop, reboot, and shut down the proxy stack. It runs on the
proxy host rather than in a container, so it stays available while the
containers are stopped.

1. Create a token file outside the repository:

   ```bash
   umask 077
   mkdir -p ~/.secrets
   openssl rand -hex 32 > ~/.secrets/proxy_control_token
   ```

2. Install the unit and start the service:

   ```bash
   sudo cp scripts/proxy-control.service /etc/systemd/system/proxy-control.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now proxy-control
   systemctl status proxy-control
   ```

3. In HA, set the control URL to `http://<PROXY_IP>:8790` and paste the token
   into the Inverter Proxy entry's Configure panel (or into the setup form).
   The token is stored only in HA's config entry.

Endpoints:

```text
GET  /health   -> {"ok": true}                    (no auth)
GET  /status   -> container states                (bearer token)
POST /command  -> {"command": "start"}            (bearer token)
```

Valid commands: `start`, `stop`, `reboot` (alias `restart`), `shutdown`.

Port `8790` has bearer-token authentication but should still be firewalled to
the trusted VLAN20. Never put the token in this repository or in a log.

## Updates

```bash
git pull
docker compose -f docker-compose.host.yml up -d --build
```

or, for macvlan:

```bash
git pull
./scripts/up.sh
```

The HA custom integration is updated separately; see
[ha-integration.md](ha-integration.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `goodwe inverter/proxy not found on ip:<host>:<port>` | The configured dongle address is wrong or unreachable; fix `drivers[].host` or re-run the HA config flow |
| Facade answers but data is stale | Check `status.devices.goodwe.age` and the core log; the dongle may be overloaded |
| HA cannot add the integration | `ping_proxy` must reach `<PROXY_IP>:8787`; verify `listen_host` and firewall |
| Writes seem delayed | Expected: `write_interval` paces writes to the slow inverter |
| Entity IDs changed | Should not happen; the HA integration updates the config entry in place |

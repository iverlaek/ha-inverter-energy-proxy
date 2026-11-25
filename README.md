# HA Inverter Energy Proxy

One cached proxy in front of your inverters and batteries.

Programs that used to hammer an inverter's Wi-Fi dongle talk to this proxy
instead. It presents a single, combined, canonical view of every device, answers
consumers in the protocol they already speak (starting with the GoodWe UDP
dongle protocol on port 8899), and translates their writes to whatever the real
device — possibly a different brand — actually needs.

The goal is simple: **the physical dongle is touched by exactly one client, this
proxy.** Everything else reads the proxy's cache.

## Why

A GoodWe Wi-Fi/LAN dongle answers one request at a time and gets flaky when
several programs poll it at once. Home Assistant, a dashboard, an automation and
a monitoring script each opening their own connection is enough to make reads
time out. This proxy polls the device on a schedule, caches the result, and
serves every consumer from that cache.

It also separates two concerns that are usually tangled together:

* **the inverter** — read its telemetry, accept its settings;
* **the battery** — accept an *"I want to charge / discharge"* request.

A consumer never has to know that, today, the battery happens to live on the
GoodWe inverter. It asks the battery part to charge at 4 kW; the battery part
decides how to make that happen.

## How it fits together

```
        consumers (Home Assistant, scripts, dashboards, ...)
                          │  GoodWe UDP :8899
                          ▼
   ┌──────────────────────────────────────────────────────────┐
   │  facade-goodwe   (its own LAN IP, stateless, no driver)   │
   └───────────────────────────┬──────────────────────────────┘
                               │  internal TCP :8787 (line JSON)
                               ▼
   ┌──────────────────────────────────────────────────────────┐
   │  core   canonical model · poller · cache · battery intent │
   │         GoodWe driver · (SolarEdge, Enphase: paused)      │
   └───────────────────────────┬──────────────────────────────┘
                               │  one poll, one client
                               ▼
                       real GoodWe dongle :8899
```

* **core** holds all brand knowledge: it polls the real devices, keeps one
  canonical snapshot, and applies writes.
* **facades** speak a consumer protocol and forward to the core. A facade holds
  no device state and no brand knowledge, so it can run in its own container,
  on its own IP address.

The same image runs either part, selected by `--mode` (see
[docs/architecture.md](docs/architecture.md) for the full story).

## Documentation

- [Deployment](docs/deployment.md) — network rules, host/macvlan setup, config reference, control API
- [Home Assistant integration](docs/ha-integration.md) — install, setup fields, fallback behavior, verification
- [Security and credentials](docs/security.md) — what is stored where, control API exposure, publishing checklist
- [Architecture](docs/architecture.md) — layers and internal protocol
- [Register map](docs/registers.md) — GoodWe/EMS registers used by the drivers

## Quick start (Docker Compose)

Two deployment shapes ship with the repo, both driven by the same image and
config:

* **macvlan** (`docker-compose.yml`) — each facade gets its own LAN IP, so you
  can point an existing consumer at that IP and leave its configuration
  otherwise untouched. This is the default and the one the steps below describe.
* **one host IP** (`docker-compose.host.yml`) — the containers share the host's
  network namespace and the facade listens on the host's own address. Simpler,
  but a port per facade. See
  [Alternative: one host IP](#alternative-one-host-ip-no-macvlan).

> **Network note:** IoT devices, including this proxy and the GoodWe dongle,
> belong on **VLAN20 (`192.168.20.0/24`)**. Do not place the proxy on the
> `192.168.55.0/24` LAN.

### macvlan

Each facade gets its own IP address on your LAN, so you can point an existing
consumer at that IP and leave its configuration otherwise untouched.

### 1. Prerequisites

* Docker Engine and the Compose plugin.
* A **wired** interface. macvlan does not work over Wi-Fi.
* Two (or more) free LAN addresses outside your DHCP pool — one for the core and
  one per facade.

### 2. Configure

```bash
cp .env.example .env
$EDITOR .env
```

Set the parent interface, subnet, gateway and the container IPs. Then set the
real inverter's address in `config/inverter_proxy.yaml`:

```yaml
drivers:
  - name: goodwe
    brand: goodwe
    host: 192.168.20.13   # your dongle
```

### 3. Bring it up

```bash
./scripts/up.sh          # host shim + docker compose up -d --build
```

`scripts/up.sh` also runs `scripts/host-macvlan-shim.sh`, which creates a small
macvlan interface on the host. macvlan deliberately blocks traffic between a
container and its parent host, so **if a consumer runs on the same machine as
this stack** (for example Home Assistant on the same box) it needs that shim to
reach the facades by IP. Re-run the shim script after changing IPs.

### 4. Point your consumer at the facade

In the GoodWe integration, replace the inverter's IP with the facade's IP
(`GOODWE_FACADE_IP`, e.g. `192.168.20.92`). Port stays `8899`.

### 5. Verify

```bash
docker compose ps
docker compose logs -f core

# read a block straight from the facade
python3 scripts/probe.py "$GOODWE_FACADE_IP" --offset 35000 --count 20
```

### Alternative: one host IP (no macvlan)

If the machine running the stack is dedicated to it, and a single emulated device
is enough, you can skip macvlan. The containers then share the host's network
namespace and the facade listens on the host's own address — no extra LAN
addresses and no host shim.

```bash
$EDITOR config/inverter_proxy.yaml   # set the dongle host and core.remote
./scripts/up-host.sh                 # docker compose -f docker-compose.host.yml up -d --build
```

Set `core.remote` to the loopback address, because the facade reaches the core
inside the host:

```yaml
core:
  listen_host: 0.0.0.0   # HA must reach the control API to hand over the inverter IP
  port: 8787
  remote: "127.0.0.1:8787"  # facades still talk to the core over loopback
```

Consumers then point at the host's own IP, port `8899`. This mode costs a port
per facade, so two facades that both expect UDP `8899` cannot coexist; use the
macvlan file when every facade needs its own address.

## Home Assistant integration

The repo ships a custom integration in `custom_components/ha_inverter_proxy`.
Copy that folder to `<HA config>/custom_components/ha_inverter_proxy` on each HA
instance and restart Home Assistant, then add **Inverter Proxy** from
**Settings → Devices & Services → Add Integration**.

The setup form asks for the proxy IP, verifies the control API on port `8787`,
reads the existing GoodWe integration, hands that inverter address to the proxy,
and changes the GoodWe integration in place to use the proxy's `8899` facade.
Entity IDs and names are preserved. If the stored GoodWe address is stale, the
proxy's currently configured inverter address is tried too; you can override
either with the **Inverter IP address** field.

The form also explains the automatic fallback: if the proxy is unreachable for
more than five minutes **and** the real inverter still answers, HA temporarily
points GoodWe back to the inverter so its status stays visible. As soon as the
proxy answers again, HA switches back automatically. Removing the integration
restores the original GoodWe address.

Multiple HA instances can each add the same proxy; they all read cached data and
the proxy remains the only client that polls or writes to the physical inverter.

See [docs/ha-integration.md](docs/ha-integration.md) for the full setup, fallback
policy, removal behavior, and troubleshooting.

## Configuration

Everything is driven by one file, `config/inverter_proxy.yaml`, mounted
read-only into every container. The deployment shape comes from `mode`, and the
Compose services override it per container on the command line.

| Key | Meaning |
| --- | --- |
| `mode` | `all` (one process), `core` (poll + TCP endpoint), `facade` (serve, forward to core) |
| `poll.interval` / `poll.ttl` | seconds between polls (default 10); age at which a reading is stale |
| `poll.min_backoff` / `max_backoff` | retry backoff bounds after a failed poll |
| `write_interval` | minimum seconds between writes to the real device (default 10) |
| `core.listen_host` / `core.port` | where the core listens (default `0.0.0.0:8787`) |
| `core.remote` | `host:port` of the core, used by facades |
| `core.device` | the device a facade fronts when it does not say |
| `drivers[]` | one entry per inverter: `name`, `brand`, `enabled`, `host`, `port`, `comm_addr`, ... |
| `battery` | `brand`, `control` (`ems` or `eco`), `rated_power_w`, optional `driver` |
| `servers[]` | one entry per facade: `kind`, `device`, `host`, `port` |

If a GoodWe poll fails, the core logs
`goodwe inverter/proxy not found on ip:<configured host>:<port>`, naming the
address from `drivers[].host` so a stale or unreachable IP is easy to spot.

Modes in more detail:

* `all` — simplest; one container polls and serves. Good for a single device or
  local experimentation.
* `core` — the brand-aware half. Polls the devices and serves the internal
  protocol on `core.port`.
* `facade` — the consumer-facing half. Speaks the consumer protocol and forwards
  to `core.remote`.

### The internal protocol

Facades and the core speak a deliberately tiny line-delimited JSON protocol over
TCP, so it can be re-implemented in any language later:

```json
{"op": "read",     "device": "goodwe", "offset": 47515, "count": 16}
{"op": "write",    "device": "goodwe", "offset": 47517, "value": 50}
{"op": "snapshot", "device": "goodwe"}
{"op": "status",   "device": "goodwe"}
{"op": "configure","device": "goodwe", "host": "192.168.20.13", "port": 8899}
{"op": "ping"}
```

## The battery is separate from the inverter

A consumer expresses a **request**, not a register write:

```python
BatteryIntent.charge(power_w=4000, deadline=...)
BatteryIntent.discharge(power_percent=50)
BatteryIntent.self_consumption()
BatteryIntent.idle()
```

The GoodWe battery realises that request one of two ways:

* `control: ems` (default) — the external-EMS registers (`47511`/`47512`). The
  request maps to an EMS mode plus a signed power setpoint.
* `control: eco` — a single all-day Eco-mode window whose power sign is the
  request, for firmware without the EMS registers.

A future battery of another brand realises the same intent over its own
interface, and no consumer changes.

## Adding a brand

The canonical model in `proxy/canonical.py` is the contract. To add a brand you
write a driver that maps its parameters onto those keys — you never edit the
core.

1. Add a register map (see `proxy/drivers/goodwe_map.py`) as data.
2. Implement an `InverterDriver` (see `proxy/drivers/goodwe.py`).
3. Register it in `DRIVERS` in `proxy/core.py` and enable it in the config.
4. If the brand brings its own battery, implement a `Battery` that realises a
   `BatteryIntent`.

SolarEdge and Enphase are already on the map as **paused** drivers
(`enabled: false`). They satisfy the interface but do nothing yet; the plan is
recorded on each class.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest -q
```

Run the proxy without Docker:

```bash
python -m proxy --config config/inverter_proxy.yaml --check
python -m proxy --config config/inverter_proxy.yaml --once
python -m proxy --config config/inverter_proxy.yaml --mode core
```

See [docs/architecture.md](docs/architecture.md) for the design and
[docs/registers.md](docs/registers.md) for the GoodWe register reference.

## Status

* **GoodWe ET/EH-family** — implemented and verified end to end against a
  GW10K-ET: polling, the canonical cache, the dongle facade and writes
  (including Time-of-Use schedules and battery intents).
* **SolarEdge, Enphase** — paused until the GoodWe path is fully settled.

## License

MIT — see [LICENSE](LICENSE).

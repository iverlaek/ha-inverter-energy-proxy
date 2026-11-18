# Architecture

## The problem

A GoodWe Wi-Fi/LAN dongle answers roughly one request at a time, and it gets
unreliable when several programs poll it at once. At the same time, a home
energy setup wants to combine more than one brand: a GoodWe hybrid inverter
today, a SolarEdge or Enphase system later, each with its own protocol and its
own quirks.

The proxy solves both by being **the single client of every physical device**,
and by presenting **one canonical view** to everyone else.

## Layers

```
proxy/
├── canonical.py     the brand-independent parameter registry + Snapshot
├── schedule.py      Time-of-Use schedules (brand-independent)
├── intents.py       the battery charge/discharge request (brand-independent)
├── poller.py        polls devices, caches one snapshot, routes writes
├── config.py        the one config file and the three deployment modes
├── core.py          wiring: factories + the all/core/facade services
├── transport/       dumb byte-moving (GoodWe UDP framing)
├── drivers/         brand knowledge: register maps + drivers
├── battery/         the battery boundary (intent -> real device)
└── server/          consumer facades + the core link between them
```

Each layer depends only on the one below it:

* a **transport** moves bytes; it knows nothing about meaning;
* a **driver** gives bytes meaning by mapping them to canonical keys;
* a **battery** accepts a request and realises it (for GoodWe, via the inverter's
  registers);
* the **poller** is the only thing that touches drivers, and the only writer of
  the shared snapshot;
* a **facade** speaks a consumer protocol and forwards to the core;
* the **core** wires everything together.

## Canonical model

`proxy/canonical.py` is the contract between brands. Every value is a key such
as `battery.soc`, `inverter.grid_power` or `inverter.tou_windows`, with a unit,
a kind and an access level. Drivers translate their own register map to and from
these keys.

Readings live in a `Snapshot`: a mapping of key to `Reading` (value + timestamp +
source). The poller fills one snapshot per cycle; every consumer reads from it.
Because the registry is data, adding a brand never means editing the core.

## Core and facade

The proxy is split so it can scale across brands **and** network identities.

* The **core** holds brand knowledge: drivers, the poller, the canonical
  snapshot, the battery.
* A **facade** holds none of that. It speaks a consumer protocol, and forwards
  every read and write to the core through a `CoreLink`.

`CoreLink` (`proxy/server/base.py`) has two implementations:

* `InProcessCore` — calls the driver and poller directly (the `all` mode, and
  the tests);
* `RemoteCore` — a small line-delimited JSON client over TCP (the `core` +
  `facade` modes).

That seam is what lets each facade run as its own container with its own IP
address, while the brand knowledge stays in one place.

### Internal protocol

```
-> {"op": "read",     "device": "goodwe", "offset": 47515, "count": 16}
<- {"ok": true, "registers": [512, 1536, ...]}

-> {"op": "write",    "device": "goodwe", "offset": 47517, "value": 50}
<- {"ok": true}

-> {"op": "snapshot", "device": "goodwe"}
<- {"ok": true, "values": {"battery.soc": {...}, ...}}

-> {"op": "status",   "device": "goodwe"}
<- {"ok": true, "status": {...}}

-> {"op": "ping"}
<- {"ok": true}
```

Errors come back as `{"ok": false, "error": "..."}`. `CoreServer` runs each
request in a thread so a slow device read does not stall other facades.

## Reads

1. The poller calls `driver.read_state()` on each due device.
2. The driver reads its register blocks once, decodes them to canonical keys,
   and keeps a raw **register image** (`{offset: value}`).
3. The poller merges the readings into the shared snapshot.
4. A facade answers a consumer read straight from the driver's image
   (`image_block`), so the real dongle is never touched by the consumer.

## Writes

1. The facade parses the consumer's raw `(offset, value)` and hands it to the
   core.
2. The driver's `decode_write` interprets it: a known register becomes a
   **canonical** write, an unknown one stays **raw**.
3. The poller applies it. Canonical Time-of-Use writes decode to
   `inverter.tou_windows`; explicit charge/discharge decodes to a
   `battery.intent`.
4. A Time-of-Use write also tells the **battery** what the schedule means
   (`observe_intent`) — the schedule is already on the device, so the battery
   records it rather than writing anything of its own.
5. An explicit charge/discharge request goes to the battery, which realises it
   (GoodWe: EMS registers by default, or a single Eco window).

## The battery boundary

A consumer never commands registers for the battery; it expresses intent:

```python
BatteryIntent.charge(power_w=4000)
BatteryIntent.discharge(power_percent=50)
BatteryIntent.self_consumption()
BatteryIntent.idle()
```

`proxy/battery/goodwe.py` realises that with the inverter's EMS registers
(`47511`/`47512`), or, with `control: eco`, a single all-day Eco-mode window.
The point is that the *battery* decides how, so a battery of another brand can
be added without touching consumers or the core.

## Polling and resilience

* One poll per `poll.interval`; readings older than `poll.ttl` are reported stale.
* A failed poll sets `online: false`, records the error, and schedules a retry
  with exponential backoff between `min_backoff` and `max_backoff`.
* Optional register blocks (device info, battery info, the second Eco layout) may
  be absent without failing the whole poll; a required block failing does fail it.

## Deployment

The same image runs every part; `--mode` chooses which.

| Mode | What it does | Where it runs |
| --- | --- | --- |
| `all` | poll + serve in one process | one container |
| `core` | poll + serve the internal TCP endpoint | `core` container |
| `facade` | serve consumers, forward to the core | one container per facade |

On a macvlan network each container gets a LAN IP, so a facade looks like a
separate box and existing consumers just change an address. macvlan blocks
host↔container traffic, so a consumer running on the same host needs the
`scripts/host-macvlan-shim.sh` interface to reach a facade by IP.

## Testing

Everything brand-specific sits behind an interface, so the whole stack runs
without a device: `tests/conftest.py` supplies an in-memory `Transport`, a
realistic GoodWe register image and a `FakeCore`. The suite covers framing,
decoding, windows, intents, the poller, the facade, the core seam, config and
the wiring — including an end-to-end poll → cache → facade read → facade write
path with no network.

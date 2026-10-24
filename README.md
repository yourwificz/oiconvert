# oiconvert

## About oiconvert
A toolkit for conversions targeting the OpenIntent 2.0.2 standard.

- `esx2oi.py` — Ekahau **.esx → OpenIntent 2.0.2** ZIP (tested primarily with Hamina)

Contributions & test reports welcome.

# esx2oi — Ekahau .esx → OpenIntent 2.0.2

Converts Ekahau **.esx** projects into **OpenIntent 2.0.2** ZIPs.

> **Compatibility scope**  
> The converter targets the official OpenIntent 2.0.2 schema and emits `openintent_version: "2.0.2"`. Schema conformance is the reference for conversion behavior; importer-specific limitations are documented separately. Regression fixtures validate against that release schema. The OpenIntent 2.0.2 output was successfully imported into **Hamina Network Planner** with and without walls. The maintainer also confirmed successful import of coverage areas and exclusions. Other tools may interpret certain fields differently; please try your target tool and report results (PRs/issues welcome).

- Floors: images + dimensions (px/m/ft), geometry Y-flipped to align with images  
- Walls & materials: used-only, names prefixed `[Imported]`, attenuation rounded to 1 decimal  
- APs: source vendor/model and coordinates; fallback for incomplete identities
- Coverage areas: inclusion and exclusion polygons, assigned to their source floor

**License:** Apache-2.0

---

## Quick start

```bash
/usr/bin/python3 /abs/path/esx2oi.py   --esx "/abs/path/in.esx"   --out "/abs/path/out.zip"
```

Options (excerpt):
- `--prefix "[Imported] "` — prefix for imported material names  
- `--all-materials` — export *all* ESX wall types (default = only those actually used)  
- `--fallback-manufacturer "ubiquiti"`  
- `--fallback-model "uap-ac-pro"`
- `--force-fallback-model` — replace every source AP model with the fallback pair (legacy behavior)

> Complete source manufacturer/model pairs are preserved, including casing and punctuation. If either field is missing, the complete fallback pair is used and reported on stderr. `--force-fallback-model` restores the previous behavior. Originals remain in `manufacturer_original` and `model_original`. Model recognition depends on the importer’s catalog. The maintainer confirmed successful model import using synthetic and real-project samples.

---

## Conversion Capabilities & Limitations

### Supported now
- **Floorplans**
  - Embedded map images in `images/...` and `map_uri` (`file://...`)
  - Dimensions in **pixels / meters / feet**; pixel *height* = ceiling height in **pixels** (not image height)
  - `rotation: 0`, `project_name`, and reserved `reference_markers`
- **Walls & Materials**
  - Floorplan-only projects are supported even when Ekahau omits wall JSON files.
    If wall segments exist, `wallPoints.json` and `wallTypes.json` remain required.
  - Export only materials actually used on walls (or all via `--all-materials`)
  - Names prefixed with `[Imported] ...`; attenuation rounded to **1 decimal**
- **Access Points**
  - Unplaced APs are retained without empty coordinates or an invented floor reference.
  - Complete source vendor/model pairs are preserved; fallback is used for incomplete identities or when explicitly forced
  - Coordinates as `coordinate_xyz` in **this order**: **pixels → meters → feet**
  - Planned AP height comes from a consistent `antennaHeight` across WLAN radios, converted to px/m/ft using the floor scale; absent heights retain the 2.5 m default
  - Invalid or conflicting radio heights are rejected; height cannot be converted to pixels on unscaled floors
  - Planned WLAN radio IDs, bands (2.4/5/6 GHz), occupied channel blocks, contiguous widths (20–320 MHz where supported) and enabled/disabled state are exported
  - Bluetooth and deleted radios are ignored. APs without planned WLAN radios omit `dot11_radios`; importer defaults may apply
  - AP hex display colors are preserved; absent colors retain the existing default
  - Power, RF chain counts and orientation are omitted rather than filled with placeholder values
  - No invented antenna object: AP models are not antenna model identifiers
- **Coverage areas**
  - `areas.json` and `exclusionAreas.json` become inclusion/exclusion polygons
  - Polygon coordinates use pixels with the same Y flip as walls and APs
  - Deleted areas are ignored; malformed polygons and unknown floors are rejected
  - Requirement profiles, area names/colors, and capacity settings are not mapped
- **Schema parity**
  - Top-level `switches: []` included for parity with some importers

### Workarounds / caveats
- **AP vendor/model recognition**
  - Some importers may hide pins/BOM when the model is unknown. Try `--force-fallback-model` with a known catalog model (default: `ubiquiti / uap-ac-pro`); originals are retained in `*_original` fields.
- **AP mounting type**
  - No standard AP mounting field exists in the pinned OI 2.0.2 schema; importer model defaults may apply. Set post-import if needed.
- **AP tilt/azimuth**
  - Not exported yet: source and destination orientation conventions need validation; set post-import if needed.

### Planned radio caveats
- ESX occupied-subchannel lists do not explicitly identify the primary 20 MHz channel. Bonded radios use the **lowest occupied subchannel** as primary and log this choice. Verify primary channels after import; manual/automatic assignment is not asserted.
- Non-contiguous channel lists, unsupported widths, mixed bands, duplicate radio slots and unknown AP references are rejected.
- OI defines `transmit_power` as per-chain power. ESX power semantics and the RF-chain count must be confirmed before conversion; spatial-stream count is not assumed to be RF-chain count. Power is currently omitted, so importers may use their defaults.
- Notes and tags have no standard AP fields in the pinned schema and remain future work.

### On the radar (non-binding)
- Source transmit power, RF chains, measured radios and exact primary-channel handling (remaining work for issue #3)
- AP antennas: models, patterns and per-radio antenna configuration
- AP installation: mounting type, azimuth/rotation, tilt and per-radio height differences
- AP metadata: notes, tags and other source properties, subject to OpenIntent representation and importer support
- Attenuation areas (types + polygons)
- Reference/scale markers
- Notes & picture notes (image annotations)
- Survey results (measured heatmaps / AP placements)

### Known importer quirks (observed)
- In a Hamina import test, channel widths differed from the OI input even with simulated AP auto-channel assignment disabled. A subsequent Hamina export used the same standard `channel_width` strings. The maintainer reports that Hamina controls widths through project-level settings and offers no per-AP width option. Exact width round-trip preservation is not supported in this tested workflow. The converter retains standard OI 2.0.2 per-radio widths; this is tracked as a Hamina interoperability limitation, with the internal cause unconfirmed.
- AP pins/BOM may not appear if the AP model is **unknown** to a tool’s catalog
- Coordinate order can matter: **pixels → meters → feet**
- Pixel dimension `height` is **ceiling height in pixels**, not the image’s pixel height

---

## Interoperability notes
- Empty `wall_segments` and `accesspoints` properties are omitted, as required by
  the OpenIntent 2.0.2 schema (`minItems: 1` when present).
- **Confirmed importer:** Hamina Network Planner (imports/exports OpenIntent `.zip` and documents supported fields).  
- **Ecosystem status:** OpenIntent is evolving. If you use another tool, please test and open an issue/PR with results and sample files.

---

## Regression tests

From the repository root:

```bash
python3 -m pip install -r requirements-test.txt
python3 -m unittest discover -s tests -v
```

Tests generate synthetic ESX archives covering floorplan-only projects, all eight
combinations of missing/empty wall files, standard walls and APs, mixed floors,
`--all-materials`, AP identity preservation/fallback, coverage geometry and exclusions, and missing or malformed input data. Successful conversions
are checked against a pinned, unmodified official OpenIntent 2.0.2 JSON schema,
including the embedded images. No network access is needed to run the tests.
The reporter's original ESX is unavailable. Wall-free conversion, coverage areas and exclusions were successfully imported into Hamina, as confirmed by the maintainer.

---

## Example

```bash
/usr/bin/python3 /abs/path/esx2oi.py   --esx "/abs/path/in.esx"   --out "/abs/path/out.zip"   --prefix "[Imported] "   --fallback-manufacturer "ubiquiti"   --fallback-model "uap-ac-pro"
```

AP model preservation is covered by synthetic regression fixtures. The maintainer confirmed successful Hamina model recognition with synthetic and real-project samples. Planned radio channels, widths, height and color have schema/regression coverage. A private Hamina export retained AP colors, heights and channel numbers; channel widths differed from the input as described under importer quirks. This does not change the converter's OI 2.0.2 width encoding.

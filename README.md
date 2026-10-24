# oiconvert

## About oiconvert
A tiny toolkit for OpenIntent conversions.

- `esx2oi.py` — Ekahau **.esx → OpenIntent 2.0.2** ZIP (tested primarily with Hamina)

Contributions & test reports welcome.

# esx2oi — Ekahau .esx → OpenIntent 2.0.2

Converts Ekahau **.esx** projects into **OpenIntent 2.0.2** ZIPs.

> **Compatibility scope**  
> The converter emits `openintent_version: "2.0.2"`; regression fixtures validate against the official 2.0.2 release schema. The OpenIntent 2.0.2 output was successfully imported into **Hamina Network Planner** using a real survey project and a derived copy without walls. The AP model and coverage-area changes described below still need a separate live import check. Other tools may interpret certain fields differently; please try your target tool and report results (PRs/issues welcome).

- Floors: images + dimensions (px/m/ft), geometry Y-flipped to align with images  
- Walls & materials: used-only, names prefixed `[Imported]`, attenuation rounded to 1 decimal  
- APs: source vendor/model and coordinates; explicit fallback for incomplete identities
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

> Source vendor/model strings are preserved, including casing and punctuation. If either is missing, the complete fallback vendor/model pair is used and reported on stderr. To force the fallback for every AP, use `--force-fallback-model`. Original values remain in `manufacturer_original` and `model_original`. Catalog matching is importer-specific; preserving a model does not guarantee that Hamina recognizes it.

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
  - Source vendor/model preserved; fallback used only for incomplete identities or when explicitly forced
  - Coordinates as `coordinate_xyz` in **this order**: **pixels → meters → feet**
  - Pixel `z` equals floor pixel “ceiling height”; meters/feet `z` = 2.5 m / 8.202 ft
  - Radio settings still use legacy placeholder values (2.4/5 GHz); source radio settings are not yet exported
  - No invented antenna model; importers can use their AP catalog defaults
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
  - Not directly importable in some tools (often set from the **model’s default**); adjust post-import if needed.
- **AP tilt/azimuth**
  - Ignored by some importers; set post-import if needed.

### On the radar (non-binding)
- Source radio channels, widths, power, and antenna configuration (remaining work for issue #3)
- Attenuation areas (types + polygons)
- Reference/scale markers
- Notes & picture notes (image annotations)
- Survey results (measured heatmaps / AP placements)

### Known importer quirks (observed)
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
The reporter's original ESX is unavailable. A real survey ESX and its wall-free derivative were successfully imported into Hamina before the AP model/coverage changes. These new changes need another live import check.

---

## Example

```bash
/usr/bin/python3 /abs/path/esx2oi.py   --esx "/abs/path/in.esx"   --out "/abs/path/out.zip"   --prefix "[Imported] "   --fallback-manufacturer "ubiquiti"   --fallback-model "uap-ac-pro"
```

# oiconvert

## About oiconvert
A tiny toolkit for OpenIntent conversions.

- `esx2oi.py` — Ekahau **.esx → OpenIntent 2.x** ZIP (tested primarily with Hamina)

Contributions & test reports welcome.

# esx2oi — Ekahau .esx → OpenIntent 2.x

Converts Ekahau **.esx** projects into **OpenIntent 2.x** ZIPs.

> **Compatibility scope**  
> The generated ZIPs aim to follow the OpenIntent 2.x schema. They are **tested primarily against Hamina Network Planner’s importer** today. Other tools may interpret certain fields differently; please try your target tool and report results (PRs/issues welcome).

- Floors: images + dimensions (px/m/ft), geometry Y-flipped to align with images  
- Walls & materials: used-only, names prefixed `[Imported]`, attenuation rounded to 1 decimal  
- APs: coordinates and minimal radio/antenna info; fallback AP model so pins/BOM (bill of materials) render in tested importer(s)

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

> Mounting type in some tools (e.g., Hamina) is **derived from AP model defaults**. To get ceiling-mount icons by default, the converter can force a known model via `--fallback-*`. Original ESX values are kept in `manufacturer_original` and `model_original`.

---

## Conversion Capabilities & Limitations

### Supported now
- **Floorplans**
  - Embedded map images in `images/...` and `map_uri` (`file://...`)
  - Dimensions in **pixels / meters / feet**; pixel *height* = ceiling height in **pixels** (not image height)
  - `rotation: 0`, `project_name`, and reserved `reference_markers`
- **Walls & Materials**
  - Export only materials actually used on walls (or all via `--all-materials`)
  - Names prefixed with `[Imported] ...`; attenuation rounded to **1 decimal**
- **Access Points**
  - AP pins visible in tested importer(s) (forces a known model by default; original vendor/model preserved)
  - Coordinates as `coordinate_xyz` in **this order**: **pixels → meters → feet**
  - Pixel `z` equals floor pixel “ceiling height”; meters/feet `z` = 2.5 m / 8.202 ft
  - Minimal radios (2.4/5 GHz) and antennas per AP
- **Schema parity**
  - Top-level `switches: []` included for parity with some importers

### Workarounds / caveats
- **AP vendor/model recognition**
  - Some importers may hide pins/BOM when the model is unknown. Use the **fallback model** (default: `ubiquiti / uap-ac-pro`) to ensure visibility; originals are retained in `*_original` fields.
- **AP mounting type**
  - Not directly importable in some tools (often set from the **model’s default**); adjust post-import if needed.
- **AP tilt/azimuth**
  - Ignored by some importers; set post-import if needed.

### On the radar (non-binding)
- Coverage/scope zones → `floorplans[].coverage_areas[]`
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
- **Confirmed importer:** Hamina Network Planner (imports/exports OpenIntent `.zip` and documents supported fields).  
- **Ecosystem status:** OpenIntent is evolving. If you use another tool, please test and open an issue/PR with results and sample files.

---

## Example

```bash
/usr/bin/python3 /abs/path/esx2oi.py   --esx "/abs/path/in.esx"   --out "/abs/path/out.zip"   --prefix "[Imported] "   --fallback-manufacturer "ubiquiti"   --fallback-model "uap-ac-pro"
```

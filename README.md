# oiconvert

## About oiconvert
A toolkit for conversions targeting the OpenIntent 2.0.2 standard.

- `esx2oi.py` — Ekahau **.esx → OpenIntent 2.0.2** ZIP (tested primarily with Hamina)
- `oi2esx.py` — experimental OpenIntent **2.0.1/2.0.2 → .esx** geometry writer; live open/save checks passed in Ekahau AI Pro 11.9.1.1

Contributions & test reports welcome.

## oi2esx — first geometry milestone (experimental)

Creates a **new** ESX archive using the record structure observed in Ekahau
schema 1.9.0 projects. It does not update an original ESX. Automated geometry
checks do **not** establish that Ekahau accepts the output or that RF predictions
match. Live open/save checks have passed for the samples described below;
other projects and versions still require application testing.

**Live validation (Ekahau AI Pro 11.9.1.1):** the maintainer opened and resaved
a two-floor conversion with 1,007 walls, inclusion/exclusion areas and six
attenuation areas across four types. Comparison confirmed preserved geometry,
material values, explicit heights and embedded images. A separate native
Hamina four-floor design with 2,702 walls, 12 prefixed material types and six
coverage polygons also survived open/save with those records unchanged. One
floor scale changed only in its final floating-point digit. Ekahau reordered
material bands, removed empty files and added built-in attenuation types in
projects without source types. These observations establish open/save behavior
for the tested samples, not RF simulation equivalence or an independently
confirmed close/reopen check. Private project files are not included.

Use a complete repository checkout (the pinned schema currently lives under
`tests/fixtures/oi-wifi.schema.json`):

```bash
python3 -m pip install -r /absolute/path/oiconvert/requirements.txt
python3 /absolute/path/oiconvert/oi2esx.py \
  --oi /absolute/path/design.zip \
  --out /absolute/path/new-design.esx \
  --geometry-only
```

The output ESX and adjacent `new-design.esx.report.json` must not already exist.
The report lists converted object counts, omitted objects and assumptions.
No source ESX, template or model library is required for this geometry milestone.
The minimal project structure was derived from existing Ekahau files; no source
history, survey data, notes, identities or unrelated images are copied.

**Supported:** embedded PNG/JPEG floor maps; physical scale; wall segments and
custom materials; coverage inclusion/exclusion polygons; attenuation polygons,
scalar per-metre loss, color and explicit top/bottom heights. Same-named types
with different properties receive different IDs; identical definitions share a
type. Missing attenuation-area heights remain absent. Material names default to
the `[Imported] ` prefix, without adding it twice to already-prefixed names.
Override with `--prefix "Custom: "` or disable it with `--prefix ""`.

Missing coverage inclusion polygons are reported and do not generate areas by
default. `--full-floor-coverage` explicitly adds a full-map rectangle only on
floors without source inclusion polygons; source exclusions remain intact.
These areas are named `Full-map coverage (fallback)` with the configured prefix
and listed in `synthesized_coverage`. They are synthetic map extents, **not**
reconstructed building outlines or recovered Hamina coverage boundaries.

Hamina may repeat each polygon vertex in pixels, metres and feet. Complete unit
streams are compared in vertex order, then a single representation is selected
(pixels, otherwise metres, otherwise feet). Export rounding is tolerated within
2 mm absolute or 0.002% relative error per coordinate. Incomplete or contradictory
unit streams are rejected; arbitrary mixtures of units across distinct vertices
are not interpreted as redundant representations. This applies to coverage,
exclusion and attenuation polygons.

**Explicit limitations:**

- APs/radios/antennas and switches are not generated. Inputs containing devices
  require `--geometry-only`; otherwise conversion fails. The report counts omissions.
- Buildings and nonzero floor rotations are rejected. Reference markers, floor
  holes, closets and survey paths are omitted and counted when present.
  For local geometry testing, `--ignore-floor-rotation` explicitly omits rotation
  metadata while retaining the image and its local geometry. Each omitted floor
  rotation is listed in `omitted_floor_rotations`; inter-floor alignment is not
  preserved. The default still rejects nonzero rotation; this option does not
  bypass building-transform rejection.
  Standalone floor `building_name` labels (without a top-level building object)
  are omitted and listed in `omitted_building_labels`; floors remain independent.
- Floor ceiling heights are not imported. Coverage uses a new empty requirement
  profile; configure the intended criteria in Ekahau.
- OI scalar attenuation is applied equally to 2.4/5/6 GHz. Reflection/diffraction
  use provisional values observed in Ekahau samples: wall 0.25/11, area 0.5/11.
  These values do not establish simulation equivalence. Source preservation
  extensions are not restored in this milestone, so they cannot override OI edits.
- Walls require explicit pixel dimensions and positive physical thickness. Flat
  loss can be converted to per-metre loss only when that thickness is supplied.
  Contradictory flat/per-metre losses are rejected (0.11 dB rounding tolerance).
- Physical dimensions must imply a uniform scale; image/pixel dimensions must
  match. No geometry is clamped or rescaled to hide inconsistent input. Cartesian
  polygon coordinates may use pixels/metres/feet; elevated polygons are rejected.
- Remote map URIs, unsafe paths, ambiguous OI documents, duplicate ZIP names,
  duplicate JSON keys and nonfinite JSON numbers are rejected. ZIP contents are
  limited to 512 MiB uncompressed.

Native Hamina test exports declare 2.0.1. Both accepted version strings are
validated against the repository's pinned **2.0.2** schema; this is not a claim
of complete support for every property in either version. Generated reports mark
each new output as unverified until that file receives its own application check.

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
  - Planned total conducted power is converted to integer per-chain dBm when an explicit RF-chain map is supplied; spatial streams are not used as RF-chain counts
  - Integrated antenna gain can supply standard `transmit_eirp`; external antenna EIRP is omitted because cable losses are unknown
  - Orientation remains unmapped
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
- OI defines `transmit_power` as per-chain power. [Juniper documents Ekahau power as total conducted power](https://www.mist.com/documentation/mist-ap-transmit-power-notation/). Convert with `per_chain = total - 10*log10(chains)`. OI 2.0.2 requires integer dBm: rounding introduces at most 0.5 dB error, with half-dB ties rounded upward.
- `--radio-chain-map` supplies explicit source manufacturer/model/band RF-chain counts. Matching is exact and independent of forced identity fallback. Without a match, per-chain power and RF-chain count are omitted with a diagnostic; there is no built-in model catalog or assumption based on spatial streams. Verify the supplied count for the actual operating radio configuration.
- `transmit_eirp = total_power + maxGain` is emitted only for a referenced integrated antenna with a known finite gain. It retains source precision. External antennas are excluded because cable losses are unknown. Importer support varies: Hamina currently documents that it ignores `transmit_eirp`; use per-chain power for it, with Disable power conversion OFF.
- Notes and tags have no standard AP fields in the pinned schema and remain future work.

### On the radar (non-binding)
- Automatic RF-chain discovery, measured radios and exact primary-channel handling (remaining work for issue #3)
- AP antennas: models, patterns and per-radio antenna configuration
- AP installation: mounting type, azimuth/rotation, tilt and per-radio height differences
- AP metadata: notes, tags and other source properties, subject to OpenIntent representation and importer support
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

## RF-chain map

Use `--radio-chain-map "/abs/path/radio-chains.json"` with a JSON array like:

```json
[
  {"manufacturer": "Example", "model": "Example AP", "band": "FREQ_5GHZ", "chains": 4}
]
```

Use the source manufacturer/model strings and one entry per band. Counts must be
positive integers; duplicate model/band entries are rejected. Keep site-specific
configuration and project files private. Antenna gain alone does not establish
the RF-chain count. Power conversion has schema and synthetic regression coverage. The maintainer
confirmed live Hamina import of the tested planned-radio powers, with differences
below 0.05 dB from the source total conducted values, consistent with integer
per-chain dBm quantization. This does not establish support for every model or importer.

## Attenuation areas

Optional `attenuationAreas.json` polygons and referenced active definitions from
`attenuationAreaTypes.json` map to standard floor `attenuation_areas`, separately
from coverage inclusions/exclusions. Polygon vertices use pixels with the same
Y flip as walls and APs. Material names use `--prefix`; source colors and explicit
`lowerEdge`/`upperEdge` become `display_color`, `bottom_height` and `top_height`
in metres. Missing bounds remain absent rather than inventing a ceiling height.

Ekahau attenuation areas use loss per metre, as described in
[Aruba's Ekahau planning guide](https://community.networking.hpe.com/blogs/wifiandrew1/2020/10/19/ekahau-wi-fi-tools-network-planning-with-aruba-access-points).
The source `attenuationFactor` maps to `rf_properties.attenuation_per_m`, rounded
to one decimal like existing wall materials. No flat loss or artificial thickness
is added. OI material loss is scalar: use 5 GHz, otherwise 2.4 GHz, otherwise
6 GHz, with a diagnostic when source band values differ. Absent loss stays absent.

Full source type fields (excluding record ID/status), including per-band loss,
reflection and diffraction, remain in material
`oiconvert_source.attenuation_area_type` (`version: 1`). This is a nonstandard
preservation extension; it does not make those RF properties standard OI fields.
Deleted areas are omitted. Active areas require a known floor, a valid polygon
and an active type. Losses and explicit heights must be finite and nonnegative;
top height must not be below bottom height. Unused types are not exported.

Schema and private geometry/material checks do not establish destination RF
simulation equivalence. Live import, especially absent top heights and material
loss handling, still needs verification.

#!/usr/bin/env python3
# Copyright 2025 oiconvert
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
esx2oi.py — Convert Ekahau .esx → OpenIntent 2.0.2 ZIP

Emits entities commonly accepted by OI 2.x importers:
- Floorplans: name, map_uri, pixel/meter/feet dimensions (pixel 'height' = ceiling height in px),
             wall_segments (Y-flipped to align with images)
- Wall materials: from ESX wallTypes, with prefix and 1-decimal rounding (used-only by default)
- Access Points: source vendor/model, planned WLAN channels/widths, height and display color
- Coverage areas: inclusion/exclusion polygons in Y-flipped pixel coordinates
- Top-level switches: [] (schema parity)

Defaults:
- openintent_version: "2.0.2"
- Include only the materials actually used by wall segments
- Prefix wall material names with "[Imported] "
- map_uri uses "file://images/..."
- Preserve source AP vendor/model; fallback: ubiquiti/uap-ac-pro when either is missing

Dropped:
- OI 1.x
- Non-WLAN infra beyond what's listed above

Usage:
  /usr/bin/python3 /abs/path/esx2oi.py \
    --esx "/abs/path/in.esx" \
    --out "/abs/path/out.zip" \
    --prefix "[Imported] " \
    --fallback-manufacturer "ubiquiti" \
    --fallback-model "uap-ac-pro"

Notes:
- Mounting, antenna patterns, orientation, power and RF chain counts are not mapped.
- Bonded radios use the lowest occupied 20 MHz subchannel as primary; verify after import.
- Original ESX AP vendor/model are preserved in manufacturer_original/model_original fields.
"""
import argparse, io, json, math, os, re, sys, unicodedata, zipfile
from statistics import median
from typing import Dict, Any, Tuple, List

try:
    from PIL import Image
except Exception as e:
    sys.stderr.write("Pillow is required: pip install pillow\n")
    raise

def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(code)

def info(msg: str) -> None:
    sys.stderr.write(f"INFO: {msg}\n")

def round1(x: float) -> float:
    try:
        return float(f"{float(x):.1f}")
    except Exception:
        return 0.0

def sanitize_filename(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\.-]+", "_", s).strip("._")
    return s or "Floor"

def open_esx(path: str) -> zipfile.ZipFile:
    if not os.path.exists(path):
        die(f"ESX not found: {path}")
    try:
        return zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile:
        die(f"Not a valid ZIP: {path}")

def read_json_from_zip(z: zipfile.ZipFile, name: str) -> Any:
    if name not in z.namelist():
        die(f"Missing required file in ESX: {name}")
    with z.open(name, "r") as fh:
        return json.load(io.TextIOWrapper(fh, encoding="utf-8"))

def load_esx_structures(p: str):
    z = open_esx(p)
    floors = read_json_from_zip(z, "floorPlans.json")["floorPlans"]
    # Ekahau may omit wall files entirely when a project has no walls.
    # Existing files are still parsed strictly; points and types remain required
    # when segments exist, rather than silently dropping walls from broken ESXs.
    members = set(z.namelist())
    wall_segments = (
        read_json_from_zip(z, "wallSegments.json")["wallSegments"]
        if "wallSegments.json" in members else []
    )
    wall_points = (
        {p["id"]: p for p in read_json_from_zip(z, "wallPoints.json")["wallPoints"]}
        if wall_segments or "wallPoints.json" in members else {}
    )
    wall_types = (
        {w["id"]: w for w in read_json_from_zip(z, "wallTypes.json")["wallTypes"]}
        if wall_segments or "wallTypes.json" in members else {}
    )
    aps = read_json_from_zip(z, "accessPoints.json")["accessPoints"] if "accessPoints.json" in z.namelist() else []
    radios = read_json_from_zip(z, "simulatedRadios.json")["simulatedRadios"] if "simulatedRadios.json" in z.namelist() else []
    meters_per_unit = {f["id"]: float(f.get("metersPerUnit") or 0.0) for f in floors}
    project_title = "Project"
    try:
        pj = read_json_from_zip(z, "project.json")["project"]
        project_title = pj.get("title") or pj.get("name") or project_title
    except Exception:
        pass
    return z, floors, wall_segments, wall_points, wall_types, aps, radios, meters_per_unit, project_title

def _attenuation_from_props(props_by_band: Dict[str, Any]) -> float:
    # prefer 5 GHz, then 2.4 GHz, then 6 GHz if present
    for band in ("FIVE", "TWO", "SIX"):
        if band in props_by_band and props_by_band[band].get("attenuationFactor") is not None:
            try:
                return float(props_by_band[band]["attenuationFactor"])
            except Exception:
                return 0.0
    return 0.0

def build_wall_materials(wall_types: Dict[str, Any], wall_segments: List[Dict[str, Any]], prefix: str, all_materials: bool):
    used_ids = {seg.get("wallTypeId") for seg in wall_segments if seg.get("wallTypeId") in wall_types}
    source_ids = (set(wall_types.keys()) if all_materials else used_ids)
    materials, id2name, seen = [], {}, set()
    for wt_id in source_ids:
        wt = wall_types.get(wt_id)
        if not wt:
            continue
        base = wt.get("name") or wt.get("key") or "Wall"
        name = f"{prefix}{base}"
        if name in seen:
            name = f"{name} ({wt_id})"
        seen.add(name)
        color = wt.get("color") or "#888888"
        thickness = float(wt.get("thickness") or 0.1)
        props_by_band = {p.get("band"): p for p in wt.get("propagationProperties", [])}
        att_per_m = _attenuation_from_props(props_by_band)
        materials.append({
            "name": name,
            "itu_material_type": "ITU_R_UNKNOWN",
            "rf_properties": {
                "attenuation_flat": round1(att_per_m * thickness),
                "attenuation_per_m": round1(att_per_m),
            },
            "thickness_m": thickness,
            "display_color": color,
        })
        id2name[wt_id] = name
    return materials, id2name

def img_bytes_as_png(z: zipfile.ZipFile, name: str) -> Tuple[bytes, Tuple[int, int]]:
    im = Image.open(io.BytesIO(z.read(name)))
    bio = io.BytesIO()
    im.save(bio, format="PNG")
    return bio.getvalue(), im.size

def choose_best_image(z: zipfile.ZipFile, target_w: int, target_h: int) -> Tuple[str, Tuple[int, int], bytes]:
    candidates = [n for n in z.namelist() if re.search(r"(?:^|/)(image-)", n, re.I)]
    best, best_score, size, blob = None, 1e18, (0, 0), b""
    for e in candidates:
        try:
            im = Image.open(io.BytesIO(z.read(e)))
            w, h = im.size
            sc = min(abs(w-target_w)+abs(h-target_h), abs(h-target_w)+abs(w-target_h))
            if sc < best_score:
                best, best_score, size = e, sc, (w, h)
        except Exception:
            continue
    if not best and candidates:
        best = candidates[0]
        im = Image.open(io.BytesIO(z.read(best)))
        size = im.size
    if not best:
        die("No suitable floor image found in ESX (looking for files prefixed with 'image-').")
    blob, _ = img_bytes_as_png(z, best)
    return best, size, blob

def build_walls_for_floor_flipped(floor_id: str, img_h: float, wall_segments, wall_points, id2pref):
    segs = []
    for seg in wall_segments:
        pts = seg.get("wallPoints", [])
        if len(pts) != 2:
            continue
        p1 = wall_points.get(pts[0]); p2 = wall_points.get(pts[1])
        if not p1 or not p2:
            continue
        loc1, loc2 = p1.get("location", {}), p2.get("location", {})
        if loc1.get("floorPlanId") != floor_id or loc2.get("floorPlanId") != floor_id:
            continue
        c1, c2 = (loc1.get("coord") or {}), (loc2.get("coord") or {})
        x1, y1 = c1.get("x"), c1.get("y")
        x2, y2 = c2.get("x"), c2.get("y")
        if None in (x1, y1, x2, y2):
            continue
        fy1, fy2 = float(img_h) - float(y1), float(img_h) - float(y2)
        pref = id2pref.get(seg.get("wallTypeId"), "[Imported] Wall")
        segs.append({
            "wall_type": pref,
            "start_point": {"x": float(x1), "y": fy1},
            "end_point": {"x": float(x2), "y": fy2},
        })
    return segs

def load_coverage_areas(z, floor_ids):
    """Read coverage polygons separately from RF attenuation and floor holes."""
    areas = {}
    for filename, key, exclusion in (
        ("areas.json", "areas", False),
        ("exclusionAreas.json", "exclusionAreas", True),
    ):
        if filename not in z.namelist():
            continue
        for item in read_json_from_zip(z, filename)[key]:
            if item.get("status") == "DELETED":
                continue
            fid = item.get("floorPlanId")
            if fid not in floor_ids:
                die(f"Coverage area in {filename} references unknown floor: {fid}")
            points = item.get("area")
            if not isinstance(points, list) or len(points) < 3:
                die(f"Coverage area in {filename} must contain at least three points")
            for point in points:
                if not isinstance(point, dict) or any(
                    type(point.get(axis)) not in (int, float)
                    or not math.isfinite(point[axis]) for axis in ("x", "y")
                ):
                    die(f"Invalid coverage area coordinates in {filename}")
            areas.setdefault(fid, []).append((points, exclusion))
    return areas


def build_coverage_for_floor(items, img_h):
    # Coverage geometry uses the same pixel origin as walls and AP positions.
    return [{
        "area": {"coordinates": [
            {"coordinate_xyz": {"x": float(p["x"]), "y": float(img_h) - float(p["y"]),
                                "z": 0.0, "unit": "pixels"}}
            for p in points
        ]},
        "exclusion": exclusion,
    } for points, exclusion in items]


def build_floor_objects(z, esx_floors, wall_segments, wall_points, id2pref, proj_prefix, meters_per_unit, project_name):
    floors_out, images, floor_img_h = [], {}, {}
    coverage = load_coverage_areas(z, {f["id"] for f in esx_floors})
    for f in esx_floors:
        name = f.get("name") or "Floor"
        target_w = int(round(f.get("width") or f.get("cropMaxX") or 0))
        target_h = int(round(f.get("height") or f.get("cropMaxY") or 0))
        _, (w, h), png_bytes = choose_best_image(z, target_w, target_h)
        image_rel = f"images/{proj_prefix}_{sanitize_filename(name)}.png"
        map_uri = f"file://{image_rel}"

        dims = [{"width": int(w), "length": int(h), "unit": "pixels", "height": float(h)}]
        mpu = float((meters_per_unit or {}).get(f.get("id")) or 0.0)
        if mpu > 0.0:
            dims.append({"width": float(w)*mpu, "length": float(h)*mpu, "unit": "meters", "height": 2.5})
            dims.append({"width": float(w)*mpu*3.28084, "length": float(h)*mpu*3.28084, "unit": "feet", "height": 8.202})
            # IMPORTANT: pixel dimension 'height' means ceiling height in pixels
            dims[0]["height"] = 2.5 / mpu

        floors_out.append({
            "name": name,
            "map_uri": map_uri,
            "dimensions": dims,
            "coverage_areas": build_coverage_for_floor(coverage.get(f.get("id"), []), h),
            "wall_segments": build_walls_for_floor_flipped(f.get("id"), h, wall_segments, wall_points, id2pref),
            "project_name": project_name,
            "rotation": 0,
            "reference_markers": [],
            "floor_id": f.get("id"),
        })
        # OI 2.x allows this property to be absent, but requires at least one
        # segment if it is present (minItems: 1).
        if not floors_out[-1]["wall_segments"]:
            del floors_out[-1]["wall_segments"]
        images[image_rel] = png_bytes
        floor_img_h[f.get("id")] = h
    return floors_out, images, floor_img_h

def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo

def frequency_to_band_channel(frequency):
    if type(frequency) not in (int, float) or not math.isfinite(frequency):
        die("Invalid planned radio frequency")
    if frequency == 2484:
        return "FREQ_2.4GHZ", 14
    if 2412 <= frequency <= 2472 and (frequency - 2412) % 5 == 0:
        return "FREQ_2.4GHZ", int((frequency - 2407) / 5)
    if 5000 < frequency < 5925 and frequency % 5 == 0:
        return "FREQ_5GHZ", int((frequency - 5000) / 5)
    if frequency == 5935:
        return "FREQ_6GHZ", 2
    if 5955 <= frequency <= 7115 and (frequency - 5955) % 20 == 0:
        return "FREQ_6GHZ", int((frequency - 5950) / 5)
    die("Unsupported planned radio frequency")


def load_radio_chain_map(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    if not isinstance(entries, list):
        die("Radio chain map must be an array")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            die("Invalid radio chain map entry")
        key = tuple(entry.get(k) for k in ("manufacturer", "model", "band"))
        if any(not isinstance(v, str) or not v.strip() for v in key):
            die("Radio chain map requires manufacturer, model and band")
        if key[2] not in ("FREQ_2.4GHZ", "FREQ_5GHZ", "FREQ_6GHZ"):
            die("Invalid radio chain map band")
        chains = entry.get("chains")
        if type(chains) is not int or chains < 1 or key in result:
            die("Invalid or duplicate radio chain map entry")
        result[key] = chains
    return result


def build_planned_radios(source, identity=("", ""), chain_map=None, antenna_types=None):
    output, seen = [], set()
    for index, radio in enumerate(source):
        slot = radio.get("accessPointIndex", index)
        if type(slot) is not int or slot < 0 or slot in seen:
            die("Invalid or duplicate planned radio index")
        seen.add(slot)
        enabled = radio.get("enabled", True)
        if type(enabled) is not bool:
            die("Invalid planned radio enabled state")
        result = {"id": slot, "radio_function": "CLIENT_ACCESS" if enabled else "DISABLED"}
        frequencies = radio.get("channelByCenterFrequencyDefinedNarrowChannels", [])
        if not isinstance(frequencies, list):
            die("Invalid planned radio channel list")
        if frequencies:
            channels = [frequency_to_band_channel(f) for f in frequencies]
            ordered = sorted(frequencies)
            if len(set(frequencies)) != len(frequencies) or len({b for b, _ in channels}) != 1:
                die("Duplicate or mixed-band planned radio frequencies")
            if any(b - a != 20 for a, b in zip(ordered, ordered[1:])):
                die("Non-contiguous planned radio channels are not supported")
            width = 20 * len(frequencies)
            if width not in (20, 40, 80, 160, 320):
                die("Unsupported planned radio channel width")
            if width > {"FREQ_2.4GHZ": 40, "FREQ_5GHZ": 160, "FREQ_6GHZ": 320}[channels[0][0]]:
                die("Unsupported channel width for planned radio band")
            # ESX describes occupied 20 MHz subchannels, not an explicit primary.
            # Use the lowest subchannel to preserve the occupied contiguous block.
            result.update(band=channels[0][0], channel=min(c for _, c in channels),
                          channel_width=f"{width}_MHz")
            if len(frequencies) > 1:
                info("Bonded radio: using lowest occupied subchannel as primary; verify after import")
        chains = (chain_map or {}).get((*identity, result.get("band")))
        if chains:
            result["mimo_chains"] = chains
        power = radio.get("transmitPower")
        if power is not None:
            if type(power) not in (int, float) or not math.isfinite(power):
                die("Invalid planned radio transmit power")
            if chains:
                per_chain = power - 10 * math.log10(chains)
                # OI 2.0.2 requires integer dBm; half-dB ties round upward.
                result["transmit_power"] = math.floor(per_chain + 0.5)
            antenna = (antenna_types or {}).get(radio.get("antennaTypeId"), {})
            gain = antenna.get("maxGain")
            # External antennas may have cable losses not represented here.
            if antenna.get("apCoupling") == "INTERNAL_ANTENNA" and gain is not None:
                if type(gain) not in (int, float) or not math.isfinite(gain):
                    die("Invalid integrated antenna gain")
                result["transmit_eirp"] = power + gain
            if not chains:
                info("Per-chain power omitted: no explicit RF-chain mapping for source model/band")
        output.append(result)
    return sorted(output, key=lambda r: r["id"])


def build_antennas(source, antenna_types):
    """Map known integrated identities; preserve unresolved source frames separately."""
    output, configurations = [], []
    bands = {"TWO": "FREQ_2.4GHZ", "FIVE": "FREQ_5GHZ", "SIX": "FREQ_6GHZ"}
    for index, radio in enumerate(source):
        parameters = {k: radio[k] for k in ("antennaTypeId", "antennaMounting",
                      "antennaDirection", "antennaTilt", "antennaHeight") if k in radio}
        if not parameters:
            continue
        slot = radio.get("accessPointIndex", index)
        antenna = (antenna_types or {}).get(radio.get("antennaTypeId"))
        record = {"radio_id": slot, "parameters": parameters}
        if antenna:
            # Names/patterns/mounting defaults are source data, not OI catalog aliases.
            record["antenna_type"] = {k: v for k, v in antenna.items()
                                      if k not in ("id", "status")}
        configurations.append(record)
        if not antenna:
            continue
        # apVendor describes the AP vendor, not necessarily an external antenna's
        # manufacturer. Only integrated antennas can safely use that identity.
        if antenna.get("apCoupling") != "INTERNAL_ANTENNA":
            info("External/unknown antenna coupling: preserving source data without inferred vendor")
            continue
        vendor, model = antenna.get("apVendor"), antenna.get("name")
        band = bands.get(antenna.get("frequencyBand"))
        if not all(isinstance(v, str) and v.strip() for v in (vendor, model)) or not band:
            info("Incomplete antenna identity/band: preserving source data only")
            continue
        frequencies = radio.get("channelByCenterFrequencyDefinedNarrowChannels") or []
        if frequencies and any(frequency_to_band_channel(f)[0] != band for f in frequencies):
            die("Referenced antenna band does not match planned radio band")
        band_out = {"band": band}
        if "maxGain" in antenna:
            gain = antenna["maxGain"]
            if type(gain) not in (int, float) or not math.isfinite(gain):
                die("Invalid antenna gain")
            band_out["gain"] = gain
        mapped = {"vendor": vendor, "model": model, "bands": [band_out]}
        if "directional" in antenna:
            if type(antenna["directional"]) is not bool:
                die("Invalid antenna directional flag")
            mapped["antenna_type"] = "DIRECTIONAL" if antenna["directional"] else "OMNIDIRECTIONAL"
        # OI has no standard radio-to-antenna reference. Band association is
        # standard; this namespaced extension retains the source slot explicitly.
        mapped["oiconvert_source"] = {"radio_id": slot}
        output.append((slot, mapped))
    try:
        json.dumps(configurations, allow_nan=False)
    except (ValueError, TypeError):
        die("Invalid non-JSON antenna source metadata")
    return ([a for _, a in sorted(output, key=lambda pair: pair[0])],
            sorted(configurations, key=lambda r: r["radio_id"]))


def build_aps(esx_aps, floor_name_by_id, floor_img_h_by_id, meters_per_unit, floor_dims_by_name, fallback_vendor, fallback_model, force_fallback=False, esx_radios=None, chain_map=None, antenna_types=None):
    planned = {}
    ap_ids = {ap.get("id") for ap in esx_aps or []}
    for radio in esx_radios or []:
        if radio.get("status") == "DELETED" or radio.get("radioTechnology") != "IEEE802_11":
            continue
        if not radio.get("accessPointId") or radio["accessPointId"] not in ap_ids:
            die("Planned radio references unknown AP")
        planned.setdefault(radio["accessPointId"], []).append(radio)
    aps_out = []
    for i, ap in enumerate(esx_aps or []):
        source_radios = planned.get(ap.get("id"), [])
        heights = {r["antennaHeight"] for r in source_radios if "antennaHeight" in r
                   and type(r["antennaHeight"]) in (int, float)
                   and math.isfinite(r["antennaHeight"]) and r["antennaHeight"] >= 0}
        if any("antennaHeight" in r and (type(r["antennaHeight"]) not in (int, float)
               or not math.isfinite(r["antennaHeight"]) or r["antennaHeight"] < 0)
               for r in source_radios):
            die("Invalid planned antenna height")
        if len(heights) > 1:
            die("Conflicting planned antenna heights cannot be represented as one AP location")
        height = next(iter(heights), 2.5)
        loc = ap.get("location") or {}
        fid = loc.get("floorPlanId") or ap.get("floorPlanId") or ap.get("floorId")
        floor_name = floor_name_by_id.get(fid) or "Floor"
        coord = (loc.get("coord") or {})
        x_px, y_px = coord.get("x"), coord.get("y")
        coords = []
        if isinstance(x_px, (int, float)) and isinstance(y_px, (int, float)):
            img_h = float(floor_img_h_by_id.get(fid) or 0.0)
            y_px = (img_h - float(y_px)) if img_h else float(y_px)
            if floor_dims_by_name and floor_name in floor_dims_by_name:
                W, H = floor_dims_by_name[floor_name]
                x_px = _clamp(x_px, 0, W); y_px = _clamp(y_px, 0, H)
            mpu = float(meters_per_unit.get(fid) or 0.0)
            if mpu > 0.0:
                x_m = float(x_px) * mpu; y_m = float(y_px) * mpu
                px_h = height / mpu
                coords = [
                    {"coordinate_xyz": {"x": float(x_px), "y": float(y_px), "z": float(px_h), "unit": "pixels"}},
                    {"coordinate_xyz": {"x": x_m, "y": y_m, "z": height, "unit": "meters"}},
                    {"coordinate_xyz": {"x": x_m*3.28084, "y": y_m*3.28084, "z": height*3.28084 if heights else 8.202, "unit": "feet"}},
                ]
            else:
                coords = [{"coordinate_xyz": {"x": float(x_px), "y": float(y_px), "z": 0.0, "unit": "pixels"}}]
                if heights:
                    # A physical height cannot be expressed in unscaled pixels.
                    del coords[0]["coordinate_xyz"]["z"]

        # Preserve catalog identifiers verbatim: casing and punctuation may matter.
        v_orig = (ap.get("vendor") or ap.get("manufacturer") or "").strip()
        m_orig = (ap.get("model") or "").strip()
        v, m = v_orig, m_orig
        if force_fallback or not (v and m):
            v, m = fallback_vendor, fallback_model
            info(f"AP {ap.get('name') or i}: using fallback {v}/{m} "
                 f"({'forced' if force_fallback else 'source vendor/model incomplete'})")

        # Power conversion follows the source model, even when identity fallback is forced.
        radios = build_planned_radios(source_radios, (v_orig, m_orig), chain_map, antenna_types)
        antennas, antenna_source = build_antennas(source_radios, antenna_types)
        color = ap.get("color") or "#4687f0"
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            die("Invalid AP display color")
        ap_out = {
            "name": ap.get("name") or f"AP-{i}",
            "floorplan_name": floor_name,
            "manufacturer": v,
            "model": m,
            "dot11_radios": radios,
            "coordinates": coords,
            "display_color": color,
            "manufacturer_original": v_orig,
            "model_original": m_orig,
        }
        if antennas:
            ap_out["antennas"] = antennas
        if antenna_source:
            ap_out["oiconvert_source"] = {"version": 1, "antenna_configurations": antenna_source}
        if not radios:
            del ap_out["dot11_radios"]
        # Unplaced/measured APs need not have a location. OI 2.0.2 allows
        # coordinates to be absent, but forbids an empty coordinates array.
        if not coords:
            del ap_out["coordinates"]
        if fid not in floor_name_by_id:
            del ap_out["floorplan_name"]
        aps_out.append(ap_out)
    return aps_out

def write_oi_zip(out_path: str, oi_data: Dict[str, Any], images: Dict[str, bytes], json_name: str = "openintent.json") -> None:
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("images/", b"")
        for rel, blob in images.items():
            z.writestr(rel, blob)
        z.writestr(json_name, json.dumps(oi_data, separators=(",", ":")).encode("utf-8"))
    info(f"Wrote {out_path}")

def main():
    ap = argparse.ArgumentParser(description="Convert Ekahau .esx → OpenIntent 2.0.2 ZIP")
    ap.add_argument("--esx", required=True, help="Absolute path to input .esx")
    ap.add_argument("--out", required=True, help="Absolute path to output .zip")
    ap.add_argument("--prefix", default="[Imported] ", help="Prefix for imported material names")
    ap.add_argument("--all-materials", action="store_true", help="Export all ESX wall types (default: only used)")
    ap.add_argument("--fallback-manufacturer", default="ubiquiti", help="AP vendor fallback")
    ap.add_argument("--fallback-model", default="uap-ac-pro", help="AP model fallback")
    ap.add_argument("--force-fallback-model", action="store_true",
                    help="Replace all AP models with the fallback vendor/model (legacy behavior)")
    ap.add_argument("--radio-chain-map", help="JSON array of source manufacturer/model/band/chains entries")
    args = ap.parse_args()

    z, esx_floors, wall_segs, wall_pts, wall_types, esx_aps, esx_radios, meters_per_unit, project_title = load_esx_structures(args.esx)
    chain_map = load_radio_chain_map(args.radio_chain_map)
    antenna_types = (
        {a["id"]: a for a in read_json_from_zip(z, "antennaTypes.json")["antennaTypes"]
         if a.get("status") != "DELETED"}
        if "antennaTypes.json" in z.namelist() else {}
    )
    proj_prefix = sanitize_filename(project_title)

    materials, id2pref = build_wall_materials(wall_types, wall_segs, args.prefix, args.all_materials)
    floors_out, images, floor_img_h = build_floor_objects(z, esx_floors, wall_segs, wall_pts, id2pref, proj_prefix, meters_per_unit, project_title)

    floor_name_by_id = {f["id"]: (f.get("name") or "Floor") for f in esx_floors}
    floor_dims_by_name = {f["name"]: (f["dimensions"][0]["width"], f["dimensions"][0]["length"]) for f in floors_out}
    aps_out = build_aps(esx_aps, floor_name_by_id, floor_img_h, meters_per_unit, floor_dims_by_name,
                        args.fallback_manufacturer, args.fallback_model, args.force_fallback_model,
                        esx_radios, chain_map, antenna_types)

    oi = {
        "openintent_version": "2.0.2",
        "floorplans": floors_out,
        "wall_materials": materials,
        "switches": [],
    }
    # Like wall_segments, accesspoints is optional but cannot be an empty array.
    if aps_out:
        oi["accesspoints"] = aps_out
    write_oi_zip(args.out, oi, images)

if __name__ == "__main__":
    main()

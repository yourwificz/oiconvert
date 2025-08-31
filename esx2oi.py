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
esx2oi.py — Convert Ekahau .esx → OpenIntent 2.x ZIP

Emits entities commonly accepted by OI 2.x importers:
- Floorplans: name, map_uri, pixel/meter/feet dimensions (pixel 'height' = ceiling height in px),
             wall_segments (Y-flipped to align with images)
- Wall materials: from ESX wallTypes, with prefix and 1-decimal rounding (used-only by default)
- Access Points: minimal radios+antennas, coordinates in pixels→meters→feet with pixel z set to pixel ceiling
- Top-level switches: [] (schema parity)

Defaults:
- openintent_version: "2.0.0"
- Include only the materials actually used by wall segments
- Prefix wall material names with "[Imported] "
- map_uri uses "file://images/..."
- Fallback AP model: ubiquiti/uap-ac-pro (so pins/BOM render in common importers)

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
- Some importers derive AP mounting type from the AP model's default. Tilt/azimuth is often ignored on import.
- Original ESX AP vendor/model are preserved in manufacturer_original/model_original fields.
"""
import argparse, io, json, os, re, sys, unicodedata, zipfile
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
    wall_segments = read_json_from_zip(z, "wallSegments.json")["wallSegments"]
    wall_points = {p["id"]: p for p in read_json_from_zip(z, "wallPoints.json")["wallPoints"]}
    wall_types = {w["id"]: w for w in read_json_from_zip(z, "wallTypes.json")["wallTypes"]}
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

def build_floor_objects(z, esx_floors, wall_segments, wall_points, id2pref, proj_prefix, meters_per_unit, project_name):
    floors_out, images, floor_img_h = [], {}, {}
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
            "coverage_areas": [],
            "wall_segments": build_walls_for_floor_flipped(f.get("id"), h, wall_segments, wall_points, id2pref),
            "project_name": project_name,
            "rotation": 0,
            "reference_markers": [],
            "floor_id": f.get("id"),
        })
        images[image_rel] = png_bytes
        floor_img_h[f.get("id")] = h
    return floors_out, images, floor_img_h

def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo

def build_aps(esx_aps, floor_name_by_id, floor_img_h_by_id, meters_per_unit, floor_dims_by_name, fallback_vendor, fallback_model):
    aps_out = []
    for i, ap in enumerate(esx_aps or []):
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
                px_h = 2.5 / mpu
                coords = [
                    {"coordinate_xyz": {"x": float(x_px), "y": float(y_px), "z": float(px_h), "unit": "pixels"}},
                    {"coordinate_xyz": {"x": x_m, "y": y_m, "z": 2.5, "unit": "meters"}},
                    {"coordinate_xyz": {"x": x_m*3.28084, "y": y_m*3.28084, "z": 8.202, "unit": "feet"}},
                ]
            else:
                coords = [{"coordinate_xyz": {"x": float(x_px), "y": float(y_px), "z": 0.0, "unit": "pixels"}}]

        # Force known model for importer visibility (keep originals as *_original)
        v_orig = (ap.get("vendor") or ap.get("manufacturer") or "").strip()
        m_orig = (ap.get("model") or "").strip()
        v, m = (fallback_vendor or "ubiquiti").lower(), (fallback_model or "uap-ac-pro").lower()

        radios = [
            {"id": 0, "radio_function": "CLIENT_ACCESS", "band": "FREQ_2.4GHZ",
             "channel": 11, "channel_width": "20_MHz", "transmit_power": 6, "mimo_chains": 2},
            {"id": 1, "radio_function": "CLIENT_ACCESS", "band": "FREQ_5GHZ",
             "channel": 36, "channel_width": "80_MHz", "transmit_power": 6, "mimo_chains": 2},
        ]
        antennas = [{
            "vendor": v, "model": m,
            "bands": [{"band": "FREQ_2.4GHZ"}, {"band": "FREQ_5GHZ"}]
        }]

        ap_out = {
            "name": ap.get("name") or f"AP-{i}",
            "floorplan_name": floor_name,
            "manufacturer": v,
            "model": m,
            "dot11_radios": radios,
            "antennas": antennas,
            "coordinates": coords,
            "orientation": {"rotation": 0, "tilt": 0},
            "display_color": "#4687f0",
            "manufacturer_original": v_orig,
            "model_original": m_orig,
        }
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
    ap = argparse.ArgumentParser(description="Convert Ekahau .esx → OpenIntent 2.x ZIP")
    ap.add_argument("--esx", required=True, help="Absolute path to input .esx")
    ap.add_argument("--out", required=True, help="Absolute path to output .zip")
    ap.add_argument("--prefix", default="[Imported] ", help="Prefix for imported material names")
    ap.add_argument("--all-materials", action="store_true", help="Export all ESX wall types (default: only used)")
    ap.add_argument("--fallback-manufacturer", default="ubiquiti", help="AP vendor fallback")
    ap.add_argument("--fallback-model", default="uap-ac-pro", help="AP model fallback")
    args = ap.parse_args()

    z, esx_floors, wall_segs, wall_pts, wall_types, esx_aps, esx_radios, meters_per_unit, project_title = load_esx_structures(args.esx)
    proj_prefix = sanitize_filename(project_title)

    materials, id2pref = build_wall_materials(wall_types, wall_segs, args.prefix, args.all_materials)
    floors_out, images, floor_img_h = build_floor_objects(z, esx_floors, wall_segs, wall_pts, id2pref, proj_prefix, meters_per_unit, project_title)

    floor_name_by_id = {f["id"]: (f.get("name") or "Floor") for f in esx_floors}
    floor_dims_by_name = {f["name"]: (f["dimensions"][0]["width"], f["dimensions"][0]["length"]) for f in floors_out}
    aps_out = build_aps(esx_aps, floor_name_by_id, floor_img_h, meters_per_unit, floor_dims_by_name,
                        args.fallback_manufacturer, args.fallback_model)

    oi = {
        "openintent_version": "2.0.0",
        "floorplans": floors_out,
        "wall_materials": materials,
        "accesspoints": aps_out,
        "switches": [],
    }
    write_oi_zip(args.out, oi, images)

if __name__ == "__main__":
    main()

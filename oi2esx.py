#!/usr/bin/env python3
# Copyright 2026 oiconvert
# SPDX-License-Identifier: Apache-2.0
"""Experimental OpenIntent 2.0.2 -> new Ekahau ESX geometry project.

This first integration milestone deliberately does not generate APs or radios.
The output structure follows inspected Ekahau schema 1.9.0 projects; opening,
saving and reopening in Ekahau is still a separate integration check.
"""
import argparse
import io
import json
import math
from pathlib import Path, PurePosixPath
import sys
import tempfile
from urllib.parse import unquote
import uuid
import zipfile

from jsonschema import Draft202012Validator
from PIL import Image

SCHEMA = Path(__file__).parent / "tests/fixtures/oi-wifi.schema.json"
FEET_PER_METER = 3.28084  # Same conversion as esx2oi; tolerate rounded OI exports.
MAX_BYTES = 512 * 1024 * 1024


class ConversionError(ValueError):
    pass


def record(**fields):
    return {**fields, "id": str(uuid.uuid4()), "status": "CREATED"}


def number(value, label, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ConversionError(f"{label} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ConversionError(f"{label} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def parse_json(blob):
    def constant(value):
        raise ConversionError(f"Invalid JSON constant: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ConversionError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(blob, parse_constant=constant, object_pairs_hook=pairs)


def load_oi(archive):
    members = archive.infolist()
    if len({m.filename for m in members}) != len(members):
        raise ConversionError("ZIP contains duplicate member names")
    if sum(m.file_size for m in members) > MAX_BYTES:
        raise ConversionError("ZIP uncompressed contents exceed 512 MiB")
    candidates = []
    for member in members:
        if member.filename.lower().endswith(".json"):
            data = parse_json(archive.read(member))
            if isinstance(data, dict) and "openintent_version" in data:
                candidates.append(data)
    if len(candidates) != 1:
        raise ConversionError("Expected exactly one OpenIntent JSON document in ZIP")
    data = candidates[0]
    if data["openintent_version"] not in ("2.0.1", "2.0.2"):
        raise ConversionError("This prototype accepts OpenIntent 2.0.1/2.0.2 only")
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    error = next(validator.iter_errors(data), None)
    if error:
        location = "/".join(map(str, error.absolute_path)) or "root"
        raise ConversionError(f"Invalid OpenIntent at {location}: {error.message}")
    if not data.get("floorplans"):
        raise ConversionError("At least one floorplan is required")
    return data


def image_path(uri):
    if not isinstance(uri, str) or not uri.startswith("file://"):
        raise ConversionError("Map must reference an embedded image using file://")
    path = unquote(uri[7:])
    parts = path.split("/")
    if (not path or path.startswith("/") or "\\" in path
            or any(p in ("", ".", "..") for p in parts) or ":" in path):
        raise ConversionError("Invalid embedded image path")
    return PurePosixPath(path).as_posix()


def read_floor(source, archive, *, ignore_floor_rotation=False):
    name = source["name"]
    if not name.strip():
        raise ConversionError("Floorplan names must not be blank")
    if source.get("rotation", 0) != 0 and not ignore_floor_rotation:
        raise ConversionError(f"Floor {name}: rotation is not supported yet")
    try:
        blob = archive.read(image_path(source.get("map_uri")))
    except KeyError as exc:
        raise ConversionError(f"Floor {name}: embedded image is missing") from exc
    with Image.open(io.BytesIO(blob)) as image:
        if image.format not in ("PNG", "JPEG"):
            raise ConversionError(f"Floor {name}: only PNG/JPEG images are supported")
        image.load()
        width, height = image.size
        image_format = image.format
    dimensions = {}
    for dim in source["dimensions"]:
        unit = dim["unit"]
        if unit in dimensions:
            raise ConversionError(f"Floor {name}: duplicate {unit} dimensions")
        number(dim["width"], f"Floor {name} width", positive=True)
        number(dim["length"], f"Floor {name} length", positive=True)
        dimensions[unit] = dim
    pixels = dimensions.get("pixels")
    if pixels and (pixels["width"] != width or pixels["length"] != height):
        raise ConversionError(f"Floor {name}: pixel dimensions disagree with embedded image")
    scales = []
    for unit, factor in (("meters", 1), ("feet", 1 / FEET_PER_METER)):
        if unit in dimensions:
            dim = dimensions[unit]
            scales.extend((dim["width"] * factor / width,
                           dim["length"] * factor / height))
    if not scales:
        raise ConversionError(f"Floor {name}: physical dimensions are required for scale")
    mpu = scales[0]
    if any(not math.isclose(s, mpu, rel_tol=1e-4, abs_tol=1e-9) for s in scales):
        raise ConversionError(f"Floor {name}: inconsistent physical dimensions or nonuniform scale")
    image = record(imageFormat=image_format, resolutionWidth=width, resolutionHeight=height)
    floor = record(name=name, width=width, height=height, metersPerUnit=mpu,
                   imageId=image["id"], gpsReferencePoints=[], floorPlanType="FSPL",
                   cropMinX=0.0, cropMinY=0.0, cropMaxX=width, cropMaxY=height,
                   rotateUpDirection="UP", tags=[])
    return floor, image, blob


def point(x, y, unit, floor):
    x = number(x, "Coordinate x")
    y = number(y, "Coordinate y")
    factor = {"pixels": 1, "meters": 1 / floor["metersPerUnit"],
              "feet": 1 / FEET_PER_METER / floor["metersPerUnit"]}.get(unit)
    if factor is None:
        raise ConversionError("Only Cartesian pixel/metre/feet coordinates are supported")
    x, y = x * factor, y * factor
    # Do not clamp geometry. Tiny export rounding at an image edge is tolerated.
    if x > floor["width"] + 0.01 or y > floor["height"] + 0.01:
        raise ConversionError(f"Coordinate lies outside floor {floor['name']}")
    return {"x": x, "y": floor["height"] - y}


def polygon(area, floor):
    representations = {}
    for coordinate in area["coordinates"]:
        xyz = coordinate.get("coordinate_xyz")
        if not xyz or coordinate.get("coordinate_lat_long"):
            raise ConversionError("Polygon requires Cartesian coordinates")
        if xyz.get("z", 0) != 0:
            raise ConversionError("Polygon vertex elevations are not supported")
        number(xyz["x"], "Polygon coordinate x")
        number(xyz["y"], "Polygon coordinate y")
        if xyz["unit"] not in ("pixels", "meters", "feet"):
            raise ConversionError("Unsupported polygon coordinate unit")
        representations.setdefault(xyz["unit"], []).append(xyz)
    # Hamina repeats each vertex in pixels/metres/feet. Do not concatenate
    # those redundant positions into a single zigzag polygon. Filtering by unit
    # also supports exporters that store complete unit streams in blocks.
    chosen = next((unit for unit in ("pixels", "meters", "feet")
                   if unit in representations), None)
    if chosen is None:
        raise ConversionError("Polygon requires Cartesian coordinates")
    selected = representations[chosen]
    if len(representations) > 1:
        if len(selected) < 3 or any(len(rows) != len(selected)
                                   for rows in representations.values()):
            raise ConversionError("Mixed-unit polygon must contain complete matching vertex representations")
        factors = {"pixels": floor["metersPerUnit"], "meters": 1,
                   "feet": 1 / FEET_PER_METER}
        for unit, rows in representations.items():
            for preferred, other in zip(selected, rows):
                for axis in ("x", "y"):
                    if not math.isclose(preferred[axis] * factors[chosen],
                                        other[axis] * factors[unit],
                                        abs_tol=0.002, rel_tol=2e-5):
                        raise ConversionError("Polygon coordinate representations disagree")
    vertices = [point(p["x"], p["y"], chosen, floor) for p in selected]
    if vertices and vertices[-1] == vertices[0]:
        vertices.pop()
    if len({(p["x"], p["y"]) for p in vertices}) < 3:
        raise ConversionError("Polygon requires at least three distinct vertices")
    twice_area = sum(a["x"] * b["y"] - b["x"] * a["y"]
                     for a, b in zip(vertices, vertices[1:] + vertices[:1]))
    if abs(twice_area) < 1e-9:
        raise ConversionError("Polygon has zero area")
    return vertices


def build_type(material, kind, warnings, prefix="[Imported] "):
    name = material.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConversionError("Material requires a nonblank name")
    rf = material.get("rf_properties", {})
    attenuation = rf.get("attenuation_per_m")
    thickness = material.get("thickness_m")
    if thickness is not None:
        thickness = number(thickness, f"Material {name} thickness", positive=True)
    flat = rf.get("attenuation_flat")
    if flat is not None:
        flat = number(flat, f"Material {name} flat attenuation")
    if attenuation is None:
        if kind != "wall" or flat is None or thickness is None:
            raise ConversionError(f"Material {name}: attenuation_per_m is required; "
                                  "flat wall loss needs an explicit positive thickness")
        attenuation = flat / thickness
    attenuation = number(attenuation, f"Material {name} attenuation")
    if kind == "wall" and thickness is None:
        raise ConversionError(f"Material {name}: explicit wall thickness is required")
    if kind == "area" and flat not in (None, 0):
        raise ConversionError(f"Material {name}: flat attenuation areas are not supported")
    if flat is not None and thickness is not None and not math.isclose(
            flat, attenuation * thickness, rel_tol=1e-4, abs_tol=0.11):
        raise ConversionError(f"Material {name}: flat and per-metre attenuation disagree")
    color = material.get("display_color", "#888888")
    imported_name = name if not prefix or name.casefold().startswith(prefix.casefold()) else prefix + name
    output = record(name=imported_name, color=color, propagationProperties=[
        {"band": band, "attenuationFactor": attenuation,
         "reflectionCoefficient": 0.25 if kind == "wall" else 0.5,
         "diffractionCoefficient": 11.0} for band in ("FIVE", "TWO", "SIX")])
    output["key"] = "oiconvert_" + output["id"]
    if kind == "wall":
        output["thickness"] = thickness
    for src, dst in (("bottom_height", "lowerEdge"), ("top_height", "upperEdge")):
        if src in material:
            output[dst] = number(material[src], f"Material {name} {src}")
    if output.get("upperEdge", math.inf) < output.get("lowerEdge", 0):
        raise ConversionError(f"Material {name}: top height is below bottom height")
    if kind == "wall":
        output.setdefault("lowerEdge", 0.0)
    if material.get("oiconvert_source"):
        warnings.add("Source preservation extensions were not restored; standard OI fields were used")
    return output


def building_height(source, floor):
    """Read height independently of slab thickness; never infer it from APs."""
    heights = {}
    for dimension in source["dimensions"]:
        if "height" in dimension:
            unit = dimension["unit"]
            factor = {"meters": 1, "feet": 1 / FEET_PER_METER,
                      "pixels": floor["metersPerUnit"]}[unit]
            heights[unit] = number(dimension["height"],
                f"Floor {source['name']} height", positive=True) * factor
    preferred = next((heights[u] for u in ("meters", "feet", "pixels") if u in heights), None)
    if preferred is None:
        raise ConversionError(f"Floor {source['name']}: building import requires a floor height")
    if any(not math.isclose(h, preferred, abs_tol=0.002, rel_tol=2e-5)
           for h in heights.values()):
        raise ConversionError(f"Floor {source['name']}: inconsistent height representations")
    return preferred


def build_buildings(sources, floors, order, lists):
    """Explicit ordering supplies information absent from native Hamina OI."""
    if not isinstance(order, dict) or not order:
        raise ConversionError("Building order must be a nonempty object of building names to floor lists")
    grouped = {}
    for source in sources:
        name = source.get("building_name")
        if name:
            grouped.setdefault(name, set()).add(source["name"])
    if set(grouped) != set(order):
        raise ConversionError("Building order must cover exactly the source building labels")
    floor_by_name = {f["name"]: f for f in floors}
    source_by_name = {s["name"]: s for s in sources}
    for name, names in order.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(names, list)
                or not names or any(not isinstance(n, str) or not n.strip() for n in names)):
            raise ConversionError("Each building requires a nonblank name and a list of floor names")
        if len(set(names)) != len(names) or set(names) != grouped[name]:
            raise ConversionError(f"Building {name}: list every member floor exactly once")
    # Verified in the supplied native Ekahau ESX and its material picker.
    # 30 dB/m * 0.5 m = 15 dB for a perpendicular ray, not 30 dB total.
    slab = record(key="FloorTypes.OfficeUS", name="Office (US)", isDefault=True,
                  propagationProperties=[{"band": band, "attenuationFactor": 30.0,
                    "reflectionCoefficient": 0.5, "diffractionCoefficient": 11.0}
                    for band in ("TWO", "FIVE", "SIX")])
    lists["floorTypes"].append(slab)
    imported = []
    for name, names in order.items():
        building = record(name=name, tags=[])
        lists["buildings"].append(building)
        summary = {"name": name, "floors_bottom_to_top": []}
        for index, floor_name in enumerate(names):
            floor = floor_by_name[floor_name]
            height = building_height(source_by_name[floor_name], floor)
            lists["buildingFloors"].append(record(floorPlanId=floor["id"],
                buildingId=building["id"], floorTypeId=slab["id"],
                floorNumber=index, height=height, thickness=0.5))
            summary["floors_bottom_to_top"].append({"floorplan": floor_name,
                "floor_number": index, "height_m": height})
        imported.append(summary)
    return {"mode": "experimental-explicit-order", "buildings": imported,
            "floor_number_semantics": "zero-based stack indices, not source level labels",
            "slab_defaults": {"material": "Office (US)", "thickness_m": 0.5,
                "attenuation_db_per_m": 30.0, "perpendicular_loss_db": 15.0,
                "bands": ["TWO", "FIVE", "SIX"], "source": "Ekahau default, not OpenIntent data"},
            "height_interpretation": "exported floor height; slab thickness stored separately",
            "alignment": "not imported; verify and align floorplans in Ekahau"}


def build_esx(oi, archive, *, geometry_only=False, ignore_floor_rotation=False,
              title="OpenIntent import", prefix="[Imported] ", full_floor_coverage=False,
              building_order=None):
    omitted = {"accesspoints": len(oi.get("accesspoints", [])),
               "switches": len(oi.get("switches", []))}
    if any(omitted.values()) and not geometry_only:
        raise ConversionError("AP/switch conversion is not implemented; use --geometry-only "
                              "to explicitly omit them")
    if oi.get("building"):
        raise ConversionError("Building transforms are not supported yet")
    warnings = {"Experimental ESX schema 1.9.0 output: this generated file requires its own Ekahau application check"}
    lists = {key: [] for key in (
        "floorPlans", "images", "wallTypes", "wallPoints", "wallSegments",
        "areas", "exclusionAreas", "attenuationAreas", "attenuationAreaTypes",
        "accessPoints", "simulatedRadios", "antennaTypes", "measuredRadios",
        "accessPointMeasurements", "surveyLookups", "notes", "pictureNotes",
        "referencePoints", "buildings", "buildingFloors", "floorTypes",
        "applicationProfiles", "deviceProfiles", "usageProfiles", "networkCapacitySettings",
        "scanDeviceInformations", "spectrumAnalyzerInformations", "wifiAdapterInformations",
        "requirements", "projectHistorys")}
    config = record(displayOptions=[], favoriteSsids=[])
    project = record(schemaVersion="1.9.0", name=title, title=title, customer="",
                     location="", responsiblePerson="", noteIds=[], tags=[],
                     projectAncestors=[], projectConfigurationId=config["id"])
    binaries = {"version": b"2.0"}
    floor_names = set()
    materials = {}
    for material in oi.get("wall_materials", []):
        name = material.get("name")
        if not name or name in materials:
            raise ConversionError("Wall material names must be present and unique")
        materials[name] = material
    # A type's full properties define sharing, not merely its display name.
    type_cache = {}
    omitted_rotations = []
    omitted_building_labels = []
    synthesized_coverage = []

    def get_type(material, kind):
        signature = (kind, json.dumps(material, sort_keys=True, allow_nan=False))
        if signature not in type_cache:
            output = build_type(material, kind, warnings, prefix)
            lists["wallTypes" if kind == "wall" else "attenuationAreaTypes"].append(output)
            type_cache[signature] = output["id"]
        return type_cache[signature]

    for source in oi["floorplans"]:
        if source["name"] in floor_names:
            raise ConversionError("Floorplan names must be unique")
        floor_names.add(source["name"])
        floor, image, blob = read_floor(source, archive,
                                       ignore_floor_rotation=ignore_floor_rotation)
        if source.get("rotation", 0) != 0:
            omitted_rotations.append({"floorplan": source["name"],
                                      "rotation": source["rotation"]})
            warnings.add("Nonzero floor rotations were explicitly omitted; local image coordinates "
                         "are retained and inter-floor alignment is not preserved")
        if source.get("building_name") and building_order is None:
            omitted_building_labels.append({"floorplan": source["name"],
                                            "building_name": source["building_name"]})
            warnings.add("Building labels were omitted; floorplans are imported independently")
        lists["floorPlans"].append(floor)
        lists["images"].append(image)
        binaries["image-" + image["id"]] = blob
        for key in ("reference_markers", "holes_in_floor", "closets", "survey_paths"):
            if source.get(key):
                omitted[key] = omitted.get(key, 0) + len(source[key])
        if (any("height" in d for d in source["dimensions"])
                and (building_order is None or not source.get("building_name"))):
            warnings.add("Floor ceiling heights are not imported in this geometry milestone")
        for segment in source.get("wall_segments", []):
            if segment["wall_type"] not in materials:
                raise ConversionError(f"Unknown wall material: {segment['wall_type']}")
            material = dict(materials[segment["wall_type"]])
            # OI segment overrides apply only where the material omits that property.
            for src, dst in (("thickness", "thickness_m"), ("height", "top_height"),
                             ("bottom_height", "bottom_height"), ("display_color", "display_color")):
                if dst not in material and src in segment:
                    material[dst] = segment[src]
            type_id = get_type(material, "wall")
            points = []
            for key in ("start_point", "end_point"):
                pos = segment[key]
                # Wall points have no unit field; require explicit pixel dimensions
                # to avoid assigning a made-up unit to a physical-only OI floor.
                if not any(d["unit"] == "pixels" for d in source["dimensions"]):
                    raise ConversionError("Walls require explicit pixel dimensions")
                points.append(record(location={"floorPlanId": floor["id"],
                    "coord": point(pos["x"], pos["y"], "pixels", floor)}))
            if points[0]["location"] == points[1]["location"]:
                warnings.add("Zero-length source wall segments were retained; review them in Ekahau")
            lists["wallPoints"].extend(points)
            lists["wallSegments"].append(record(wallPoints=[p["id"] for p in points],
                wallTypeId=type_id, originType="WALL_TOOL"))
        def add_coverage(vertices, excluded, name):
            output = record(floorPlanId=floor["id"], name=name, area=vertices)
            if not excluded:
                if not lists["requirements"]:
                    lists["requirements"].append(record(key="oiconvert_geometry",
                        name="Imported coverage (no requirement criteria)",
                        noiseType="AUTOMATIC", isDefault=True, criteria=[]))
                output.update(requirementId=lists["requirements"][0]["id"],
                              noteIds=[], capacityItems=[], color="#2980B9")
                warnings.add("Coverage polygons use an empty requirement profile; configure criteria in Ekahau")
            lists["exclusionAreas" if excluded else "areas"].append(output)

        coverage_areas = source.get("coverage_areas", [])
        for index, coverage in enumerate(coverage_areas, 1):
            excluded = coverage.get("exclusion", False)
            add_coverage(polygon(coverage["area"], floor), excluded,
                         f"{'Exclusion' if excluded else 'Coverage'} {index}")
        if not any(not c.get("exclusion", False) for c in coverage_areas):
            if full_floor_coverage:
                add_coverage([{"x": 0.0, "y": 0.0}, {"x": floor["width"], "y": 0.0},
                              {"x": floor["width"], "y": floor["height"]},
                              {"x": 0.0, "y": floor["height"]}], False,
                             f"{prefix}Full-map coverage (fallback)")
                synthesized_coverage.append({"floorplan": floor["name"],
                                              "kind": "full-map rectangle"})
                warnings.add("Full-map coverage rectangles were explicitly synthesized; "
                             "they are not source coverage boundaries")
            else:
                warnings.add(f"Floor {floor['name']}: no source coverage inclusion polygon; "
                             "no coverage area was generated")
        for area in source.get("attenuation_areas", []):
            lists["attenuationAreas"].append(record(floorPlanId=floor["id"],
                attenuationAreaTypeId=get_type(area["area_material"], "area"),
                area=polygon(area["area"], floor)))
    if lists["wallTypes"] or lists["attenuationAreaTypes"]:
        warnings.add("Scalar OI attenuation is applied to all three bands; RF equivalence is not established")
        warnings.add("Reflection/diffraction use provisional Ekahau sample defaults (wall 0.25/11, area 0.5/11)")
    building_report = None
    if building_order is not None:
        building_report = build_buildings(oi["floorplans"], lists["floorPlans"], building_order, lists)
        warnings.add("Building floor order is explicitly supplied, not recovered from the export")
        warnings.add("Slabs use the Ekahau Office (US) fallback: 30 dB/m, 0.5 m, all bands; "
                     "these are not imported RF properties")
        warnings.add("Building import is experimental; floor alignment is not imported and building RF equivalence "
                     "requires application verification")
    output = {key + ".json": {key: value} for key, value in lists.items()}
    output["project.json"] = {"project": project}
    output["projectConfiguration.json"] = {"projectConfiguration": config}
    report = {"status": "experimental-unverified-in-ekahau",
              "scope": "geometry-with-buildings" if building_report else "geometry-only",
              "counts": {k: len(v) for k, v in lists.items() if v},
              "omitted": {k: v for k, v in omitted.items() if v},
              "omitted_floor_rotations": omitted_rotations,
              "omitted_building_labels": omitted_building_labels,
              "synthesized_coverage": synthesized_coverage,
              "building_import": building_report,
              "warnings": sorted(warnings)}
    return output, binaries, report


def convert(source, destination, *, geometry_only=False, ignore_floor_rotation=False,
            prefix="[Imported] ", full_floor_coverage=False, building_order=None):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ConversionError("Input and output paths must differ")
    if destination.exists():
        raise ConversionError("Output already exists; choose a new path")
    report_path = Path(str(destination) + ".report.json")
    if report_path.exists():
        raise ConversionError("Report already exists; choose a new output path")
    with zipfile.ZipFile(source) as archive:
        oi = load_oi(archive)
        output, binaries, report = build_esx(oi, archive, geometry_only=geometry_only,
                                            ignore_floor_rotation=ignore_floor_rotation,
                                            prefix=prefix, full_floor_coverage=full_floor_coverage,
                                            building_order=building_order,
                                            title=source.stem)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    report_temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as fh:
            temporary = Path(fh.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, blob in binaries.items():
                archive.writestr(name, blob)
            for name, value in output.items():
                archive.writestr(name, json.dumps(value, allow_nan=False, separators=(",", ":")))
        with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent,
                                         delete=False, encoding="utf-8") as fh:
            report_temp = Path(fh.name)
            fh.write(json.dumps(report, indent=2) + "\n")
        # Publish complete files without replacing any concurrent writer's file.
        import os
        os.link(temporary, destination)
        try:
            os.link(report_temp, report_path)
        except OSError:
            destination.unlink()
            raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if report_temp is not None:
            report_temp.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oi", required=True, help="Input OpenIntent 2.0.1/2.0.2 ZIP")
    parser.add_argument("--out", required=True, help="New output ESX path (must not exist)")
    parser.add_argument("--geometry-only", action="store_true",
                        help="Explicitly omit APs and switches; report all omissions")
    parser.add_argument("--ignore-floor-rotation", action="store_true",
                        help="Explicitly omit floor rotation metadata, retaining local image "
                             "coordinates; report each omitted rotation")
    parser.add_argument("--prefix", default="[Imported] ",
                        help="Material name prefix; already-prefixed names are not prefixed twice")
    parser.add_argument("--full-floor-coverage", action="store_true",
                        help="Explicitly add a full-map rectangle on floors without inclusion "
                             "polygons; preserve source exclusions and report synthesized areas")
    parser.add_argument("--building-order", help="Enable experimental buildings using a JSON "
                        "object mapping each source building name to its floor names, bottom to top")
    args = parser.parse_args()
    try:
        building_order = parse_json(Path(args.building_order).read_text()) if args.building_order else None
        if args.building_order and not isinstance(building_order, dict):
            raise ConversionError("Building order file must contain an object")
        report = convert(args.oi, args.out, geometry_only=args.geometry_only,
                         ignore_floor_rotation=args.ignore_floor_rotation,
                         prefix=args.prefix, full_floor_coverage=args.full_floor_coverage,
                         building_order=building_order)
    except (ConversionError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    print(f"Wrote {args.out} and {args.out}.report.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

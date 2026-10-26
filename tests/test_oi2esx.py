"""Geometry writer contract checks; application compatibility is a live check."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

import oi2esx


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        image = io.BytesIO()
        Image.new("RGB", (100, 80), "white").save(image, "PNG")
        self.image = image.getvalue()
        self.material = {"name": "Custom wall", "thickness_m": 0.2,
                         "rf_properties": {"attenuation_per_m": 20,
                                           "attenuation_flat": 4},
                         "display_color": "#123456"}
        self.floor = {"name": "Ground", "map_uri": "file://images/ground.png",
                      "dimensions": [{"unit": "pixels", "width": 100, "length": 80},
                                     {"unit": "meters", "width": 10, "length": 8}],
                      "wall_segments": [{"wall_type": "Custom wall",
                                         "start_point": {"x": 10, "y": 20},
                                         "end_point": {"x": 60, "y": 30}}]}
        self.oi = {"openintent_version": "2.0.2", "floorplans": [self.floor],
                   "wall_materials": [self.material], "switches": []}

    def write_input(self, extras=None):
        source = self.directory / "input.zip"
        with zipfile.ZipFile(source, "w") as archive:
            # Hamina's document name differs from oiconvert's; warning JSON is normal.
            archive.writestr("openIntent_Project.json", json.dumps(self.oi))
            archive.writestr("export-warnings.json", "[]")
            archive.writestr("images/ground.png", self.image)
            for name, data in (extras or {}).items():
                archive.writestr(name, data)
        return source

    def convert(self, geometry_only=False, ignore_floor_rotation=False, **options):
        destination = self.directory / "output.esx"
        report = oi2esx.convert(self.write_input(), destination, geometry_only=geometry_only,
                               ignore_floor_rotation=ignore_floor_rotation, **options)
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("version"), b"2.0")
            files = {name[:-5]: json.loads(archive.read(name))[name[:-5]]
                     for name in archive.namelist() if name.endswith(".json")}
            for image in files["images"]:
                self.assertEqual(archive.read("image-" + image["id"]), self.image)
        return files, report

    @staticmethod
    def area(unit="pixels"):
        factor = 0.1 if unit == "meters" else 1
        return {"coordinates": [{"coordinate_xyz": {"x": x * factor,
                "y": y * factor, "z": 0, "unit": unit}} for x, y in ((10, 20), (60, 20), (60, 50))]}

    def test_new_project_links_scale_image_and_flipped_wall(self):
        files, report = self.convert()
        floor = files["floorPlans"][0]
        self.assertEqual(floor["metersPerUnit"], 0.1)
        self.assertEqual(floor["imageId"], files["images"][0]["id"])
        self.assertEqual(files["project"]["projectConfigurationId"], files["projectConfiguration"]["id"])
        points = {p["id"]: p for p in files["wallPoints"]}
        segment = files["wallSegments"][0]
        self.assertEqual(segment["wallTypeId"], files["wallTypes"][0]["id"])
        self.assertEqual([points[p]["location"]["coord"] for p in segment["wallPoints"]],
                         [{"x": 10, "y": 60}, {"x": 60, "y": 50}])
        self.assertTrue(all(points[p]["location"]["floorPlanId"] == floor["id"]
                            for p in segment["wallPoints"]))
        self.assertEqual(report["counts"]["wallSegments"], 1)

    def test_geometry_round_trip_through_existing_forward_converter(self):
        self.floor["coverage_areas"] = [{"area": self.area()},
                                        {"area": self.area("meters"), "exclusion": True}]
        self.floor["attenuation_areas"] = [{"area": self.area(), "area_material": {
            "name": "Closet", "rf_properties": {"attenuation_per_m": 8},
            "bottom_height": 0.3, "top_height": 3.3}}]
        self.convert()
        result = self.directory / "back.zip"
        command = subprocess.run([sys.executable, str(Path(oi2esx.__file__).with_name("esx2oi.py")),
            "--esx", str(self.directory / "output.esx"), "--out", str(result), "--prefix", ""],
            capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        with zipfile.ZipFile(result) as archive:
            oi = json.loads(archive.read("openintent.json"))
        floor = oi["floorplans"][0]
        expected = copy.deepcopy(self.floor["wall_segments"])
        for segment in expected:
            segment["wall_type"] = "[Imported] " + segment["wall_type"]
        self.assertEqual(floor["wall_segments"], expected)
        self.assertEqual(floor["coverage_areas"][0]["area"], self.area())
        self.assertTrue(floor["coverage_areas"][1]["exclusion"])
        material = floor["attenuation_areas"][0]["area_material"]
        self.assertEqual((material["bottom_height"], material["top_height"]), (0.3, 3.3))
        self.assertEqual(material["rf_properties"]["attenuation_per_m"], 8)

    def test_same_named_area_types_with_distinct_heights_do_not_share(self):
        material = {"name": "Closet", "rf_properties": {"attenuation_per_m": 8},
                    "bottom_height": 0, "top_height": 3}
        self.floor["attenuation_areas"] = [
            {"area": self.area(), "area_material": material},
            {"area": self.area(), "area_material": {**material, "top_height": 4}},
            {"area": self.area(), "area_material": copy.deepcopy(material)}]
        files, _ = self.convert()
        self.assertEqual(len(files["attenuationAreaTypes"]), 2)
        ids = [a["attenuationAreaTypeId"] for a in files["attenuationAreas"]]
        self.assertEqual(ids[0], ids[2])
        self.assertNotEqual(ids[0], ids[1])

    def test_absent_area_height_remains_absent(self):
        self.floor["attenuation_areas"] = [{"area": self.area(), "area_material": {
            "name": "Closet", "rf_properties": {"attenuation_per_m": 0}}}]
        files, _ = self.convert()
        material = files["attenuationAreaTypes"][0]
        self.assertNotIn("upperEdge", material)
        self.assertNotIn("lowerEdge", material)
        self.assertEqual(material["propagationProperties"][0]["attenuationFactor"], 0)

    def test_imported_material_prefix_and_no_double_prefix(self):
        self.floor["attenuation_areas"] = [{"area": self.area(), "area_material": {
            "name": "[Imported] Closet", "rf_properties": {"attenuation_per_m": 8}}}]
        files, _ = self.convert()
        self.assertEqual(files["wallTypes"][0]["name"], "[Imported] Custom wall")
        self.assertEqual(files["attenuationAreaTypes"][0]["name"], "[Imported] Closet")

    def test_prefix_can_be_disabled(self):
        files, _ = self.convert(prefix="")
        self.assertEqual(files["wallTypes"][0]["name"], "Custom wall")

    def test_missing_coverage_is_reported_without_inventing_polygon(self):
        files, report = self.convert()
        self.assertEqual(files["areas"], [])
        self.assertEqual(report["synthesized_coverage"], [])
        self.assertTrue(any("no source coverage" in w for w in report["warnings"]))

    def multi_unit_area(self, blocked=False):
        pixels = self.area()["coordinates"]
        metres = self.area("meters")["coordinates"]
        feet = copy.deepcopy(metres)
        for coordinate in feet:
            xyz = coordinate["coordinate_xyz"]
            xyz.update(x=xyz["x"] * 3.28084, y=xyz["y"] * 3.28084, unit="feet")
        return {"coordinates": pixels + metres + feet if blocked else
                [c for triplet in zip(pixels, metres, feet) for c in triplet]}

    def test_redundant_unit_streams_preserve_one_vertex_per_physical_position(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                self.floor["coverage_areas"] = [{"area": self.multi_unit_area(blocked)}]
                files, _ = self.convert()
                self.assertEqual(files["areas"][0]["area"], [
                    {"x": 10, "y": 60}, {"x": 60, "y": 60}, {"x": 60, "y": 30}])
                (self.directory / "output.esx").unlink()
                (self.directory / "output.esx.report.json").unlink()

    def test_conflicting_or_incomplete_unit_representations_are_rejected(self):
        area = self.multi_unit_area()
        area["coordinates"][1]["coordinate_xyz"]["x"] += 0.1
        self.floor["coverage_areas"] = [{"area": area}]
        with self.assertRaisesRegex(oi2esx.ConversionError, "representations disagree"):
            self.convert()
        self.floor["coverage_areas"][0]["area"] = self.multi_unit_area()
        self.floor["coverage_areas"][0]["area"]["coordinates"].pop()
        with self.assertRaisesRegex(oi2esx.ConversionError, "complete matching"):
            self.convert()

    def test_export_rounding_is_tolerated_but_pixel_vertices_are_preserved(self):
        area = self.multi_unit_area()
        area["coordinates"][1]["coordinate_xyz"]["x"] += 0.0013
        self.floor["coverage_areas"] = [{"area": area}]
        files, _ = self.convert()
        self.assertEqual(files["areas"][0]["area"][0], {"x": 10, "y": 60})

    def test_multirepresentation_attenuation_polygon_uses_same_normalization(self):
        self.floor["attenuation_areas"] = [{"area": self.multi_unit_area(),
            "area_material": {"name": "Closet", "rf_properties": {"attenuation_per_m": 8}}}]
        files, _ = self.convert()
        self.assertEqual(len(files["attenuationAreas"][0]["area"]), 3)

    def test_explicit_full_map_fallback_keeps_exclusions_and_has_valid_requirement(self):
        self.floor["coverage_areas"] = [{"area": self.area(), "exclusion": True}]
        files, report = self.convert(full_floor_coverage=True)
        self.assertEqual(len(files["exclusionAreas"]), 1)
        self.assertEqual(files["areas"][0]["area"], [
            {"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 80}, {"x": 0, "y": 80}])
        self.assertEqual(files["areas"][0]["requirementId"], files["requirements"][0]["id"])
        self.assertIn("fallback", files["areas"][0]["name"])
        self.assertEqual(report["synthesized_coverage"], [
            {"floorplan": "Ground", "kind": "full-map rectangle"}])

    def test_full_map_fallback_does_not_replace_explicit_source_coverage(self):
        self.floor["coverage_areas"] = [{"area": self.area()}]
        files, report = self.convert(full_floor_coverage=True)
        self.assertEqual(len(files["areas"]), 1)
        self.assertEqual(len(files["areas"][0]["area"]), 3)
        self.assertEqual(report["synthesized_coverage"], [])

    def test_device_omission_requires_explicit_option_and_is_reported(self):
        self.oi["accesspoints"] = [{"name": "AP-1"}]
        with self.assertRaisesRegex(oi2esx.ConversionError, "geometry-only"):
            self.convert()
        self.assertFalse((self.directory / "output.esx").exists())
        files, report = self.convert(geometry_only=True)
        self.assertEqual(report["omitted"], {"accesspoints": 1})
        self.assertEqual(files["accessPoints"], [])

    def test_native_hamina_201_version_and_document_filename(self):
        self.oi["openintent_version"] = "2.0.1"
        self.convert()

    def test_zero_length_source_wall_is_preserved_and_reported(self):
        segment = self.floor["wall_segments"][0]
        segment["end_point"] = dict(segment["start_point"])
        files, report = self.convert()
        self.assertEqual(len(files["wallSegments"]), 1)
        self.assertTrue(any("Zero-length" in message for message in report["warnings"]))

    def test_multifloor_area_assignment_and_unique_ids(self):
        upper = copy.deepcopy(self.floor)
        upper["name"] = "Upper"
        upper["coverage_areas"] = [{"area": self.area(), "exclusion": True}]
        self.oi["floorplans"].append(upper)
        files, _ = self.convert()
        self.assertEqual(files["exclusionAreas"][0]["floorPlanId"], files["floorPlans"][1]["id"])
        records = [r for value in files.values() for r in
                   (value if isinstance(value, list) else [value]) if isinstance(r, dict) and "id" in r]
        self.assertEqual(len(records), len({r["id"] for r in records}))

    def test_bad_scale_and_image_dimensions_fail_without_output(self):
        for key, value in (("width", 11), ("length", 0)):
            with self.subTest(key=key):
                self.floor["dimensions"][1][key] = value
                with self.assertRaises(oi2esx.ConversionError):
                    self.convert()
                self.floor["dimensions"][1][key] = 10 if key == "width" else 8
        self.floor["dimensions"][0]["width"] = 99
        with self.assertRaisesRegex(oi2esx.ConversionError, "image"):
            self.convert()
        self.assertFalse((self.directory / "output.esx").exists())

    def test_unsafe_and_remote_image_paths_fail(self):
        for uri in ("https://example.com/a.png", "file://../a.png", "file:///etc/passwd",
                    "file://images/%2e%2e/a.png", "file://images\\a.png"):
            with self.subTest(uri=uri):
                self.floor["map_uri"] = uri
                with self.assertRaises(oi2esx.ConversionError):
                    self.convert()

    def test_flat_wall_loss_needs_real_thickness(self):
        self.material["rf_properties"] = {"attenuation_flat": 4}
        files, _ = self.convert()
        self.assertEqual(files["wallTypes"][0]["propagationProperties"][0]["attenuationFactor"], 20)
        (self.directory / "output.esx").unlink()
        (self.directory / "output.esx.report.json").unlink()
        del self.material["thickness_m"]
        with self.assertRaisesRegex(oi2esx.ConversionError, "thickness"):
            self.convert()

    def test_unsupported_transforms_and_dangling_material_fail(self):
        self.floor["rotation"] = 90
        with self.assertRaisesRegex(oi2esx.ConversionError, "rotation"):
            self.convert()
        self.floor["rotation"] = 0
        self.oi["wall_materials"] = []
        with self.assertRaisesRegex(oi2esx.ConversionError, "Unknown wall"):
            self.convert()

    def test_explicit_rotation_omission_preserves_local_geometry_and_reports_value(self):
        self.floor["rotation"] = 0.08595270926749397
        files, report = self.convert(ignore_floor_rotation=True)
        self.assertEqual(report["omitted_floor_rotations"], [
            {"floorplan": "Ground", "rotation": self.floor["rotation"]}])
        self.assertEqual(files["wallPoints"][0]["location"]["coord"], {"x": 10, "y": 60})
        self.assertEqual(files["floorPlans"][0]["metersPerUnit"], 0.1)
        self.assertEqual(self.floor["rotation"], 0.08595270926749397)

    def test_building_label_is_reported_without_claiming_building_transform_support(self):
        self.floor["building_name"] = "Main"
        files, report = self.convert()
        self.assertEqual(len(files["floorPlans"]), 1)
        self.assertEqual(report["omitted_building_labels"], [
            {"floorplan": "Ground", "building_name": "Main"}])

    def test_real_building_object_still_rejected_when_ignoring_rotation(self):
        self.oi["building"] = {"name": "Main"}
        with self.assertRaisesRegex(oi2esx.ConversionError, "Building transforms"):
            self.convert(ignore_floor_rotation=True)

    def test_existing_output_and_report_are_not_overwritten(self):
        destination = self.directory / "output.esx"
        destination.write_bytes(b"keep me")
        with self.assertRaisesRegex(oi2esx.ConversionError, "already exists"):
            self.convert()
        self.assertEqual(destination.read_bytes(), b"keep me")
        destination.unlink()
        report = self.directory / "output.esx.report.json"
        report.write_text("keep report")
        with self.assertRaisesRegex(oi2esx.ConversionError, "Report already"):
            self.convert()
        self.assertFalse(destination.exists())
        self.assertEqual(report.read_text(), "keep report")

    def test_ambiguous_payload_and_duplicate_json_keys_fail(self):
        source = self.write_input({"another.json": json.dumps(self.oi)})
        with zipfile.ZipFile(source) as archive:
            with self.assertRaisesRegex(oi2esx.ConversionError, "exactly one"):
                oi2esx.load_oi(archive)
        with self.assertRaisesRegex(oi2esx.ConversionError, "Duplicate JSON"):
            oi2esx.parse_json('{"openintent_version":"2.0.1","openintent_version":"2.0.2"}')


if __name__ == "__main__":
    unittest.main()

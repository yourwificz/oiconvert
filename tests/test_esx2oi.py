"""Synthetic ESX regressions; run with python -m unittest discover -s tests -v."""
import io
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from jsonschema import Draft202012Validator
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((Path(__file__).parent / "fixtures/oi-wifi.schema.json").read_text())
WALL_FILES = ("wallSegments.json", "wallPoints.json", "wallTypes.json")


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "input.esx"
        self.output = Path(self.temp.name) / "output.zip"
        image = io.BytesIO()
        Image.new("RGB", (100, 80), "white").save(image, format="PNG")
        self.image = image.getvalue()
        self.files = {
            "project.json": {"project": {"title": "Test Project"}},
            "floorPlans.json": {"floorPlans": [
                {"id": "floor-1", "name": "Ground", "width": 100,
                 "height": 80, "metersPerUnit": 0.1}
            ]},
            "projectHistorys.json": {"projectHistorys": [
                {"productVersion": "11.0.2.219", "schemaVersion": "1.6.0"}
            ]},
            "image-floor.png": self.image,
        }

    def add_walls(self):
        self.files.update({
            "wallSegments.json": {"wallSegments": [
                {"wallTypeId": "brick", "wallPoints": ["a", "b"]}
            ]},
            "wallPoints.json": {"wallPoints": [
                {"id": point, "location": {"floorPlanId": "floor-1",
                 "coord": {"x": x, "y": y}}}
                for point, x, y in [("a", 10, 20), ("b", 60, 30)]
            ]},
            "wallTypes.json": {"wallTypes": [
                {"id": "brick", "name": "Brick", "thickness": 0.2,
                 "color": "#aabbcc", "propagationProperties": [
                     {"band": "FIVE", "attenuationFactor": 12.34}
                 ]}
            ]},
        })

    def convert(self, *options, success=True):
        self.output.unlink(missing_ok=True)
        with zipfile.ZipFile(self.source, "w") as archive:
            for name, value in self.files.items():
                archive.writestr(name, value if isinstance(value, bytes) else json.dumps(value))
        result = subprocess.run(
            [sys.executable, str(ROOT / "esx2oi.py"), "--esx", str(self.source),
             "--out", str(self.output), *options], capture_output=True, text=True,
        )
        if not success:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())
            return result
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            data = json.loads(archive.read("openintent.json"))
            # Validate the complete payload against the unmodified upstream schema.
            Draft202012Validator(SCHEMA).validate(data)
            self.assertEqual(data["openintent_version"], "2.0.2")
            self.assertEqual(data["switches"], [])
            for floor in data["floorplans"]:
                path = floor["map_uri"].removeprefix("file://")
                with Image.open(io.BytesIO(archive.read(path))) as image:
                    self.assertEqual(image.size, (100, 80))
                    self.assertEqual(image.format, "PNG")
        return data

    def test_no_walls_all_combinations_of_absent_and_empty_files(self):
        # Covers all eight possibilities without assuming which Ekahau omits.
        for present in itertools.product((False, True), repeat=3):
            with self.subTest(present=present):
                for name, exists in zip(WALL_FILES, present):
                    self.files.pop(name, None)
                    if exists:
                        self.files[name] = {name.removesuffix(".json"): []}
                data = self.convert()
                self.assertEqual(data["wall_materials"], [])
                self.assertNotIn("accesspoints", data)
                floor = data["floorplans"][0]
                self.assertNotIn("wall_segments", floor)
                self.assertEqual(floor["name"], "Ground")
                self.assertEqual(floor["dimensions"], [
                    {"width": 100, "length": 80, "unit": "pixels", "height": 25.0},
                    {"width": 10.0, "length": 8.0, "unit": "meters", "height": 2.5},
                    {"width": 32.8084, "length": 26.24672, "unit": "feet", "height": 8.202},
                ])

    def test_standard_project_with_walls_and_ap(self):
        self.add_walls()
        self.files["accessPoints.json"] = {"accessPoints": [
            {"name": "AP-1", "vendor": "Original", "model": "Model",
             "location": {"floorPlanId": "floor-1", "coord": {"x": 50, "y": 10}}}
        ]}
        data = self.convert()
        self.assertEqual(data["floorplans"][0]["wall_segments"], [{
            "wall_type": "[Imported] Brick", "start_point": {"x": 10.0, "y": 60.0},
            "end_point": {"x": 60.0, "y": 50.0},
        }])
        self.assertEqual(data["wall_materials"], [{
            "name": "[Imported] Brick", "itu_material_type": "ITU_R_UNKNOWN",
            "rf_properties": {"attenuation_flat": 2.5, "attenuation_per_m": 12.3},
            "thickness_m": 0.2, "display_color": "#aabbcc",
        }])
        ap = data["accesspoints"][0]
        self.assertEqual(ap["floorplan_name"], "Ground")
        self.assertEqual(ap["manufacturer_original"], "Original")
        self.assertEqual(ap["model_original"], "Model")
        self.assertEqual(ap["coordinates"][0]["coordinate_xyz"],
                         {"x": 50.0, "y": 70.0, "z": 25.0, "unit": "pixels"})
        self.assertEqual(ap["coordinates"][1]["coordinate_xyz"],
                         {"x": 5.0, "y": 7.0, "z": 2.5, "unit": "meters"})

    def test_unused_materials_without_walls(self):
        self.add_walls()
        self.files.pop("wallSegments.json")
        self.files.pop("wallPoints.json")
        self.assertEqual(self.convert()["wall_materials"], [])
        data = self.convert("--all-materials")
        self.assertEqual(data["wall_materials"][0]["name"], "[Imported] Brick")
        self.assertNotIn("wall_segments", data["floorplans"][0])

    def test_ap_without_walls(self):
        self.files["accessPoints.json"] = {"accessPoints": [
            {"name": "AP-1", "location": {
                "floorPlanId": "floor-1", "coord": {"x": 50, "y": 10}}}
        ]}
        data = self.convert()
        self.assertEqual(data["accesspoints"][0]["name"], "AP-1")
        self.assertNotIn("wall_segments", data["floorplans"][0])

    def test_explicit_empty_aps(self):
        self.files["accessPoints.json"] = {"accessPoints": []}
        self.assertNotIn("accesspoints", self.convert())

    def test_unplaced_aps_have_no_invented_floor_or_empty_coordinates(self):
        for location in (None, {}, {"floorPlanId": "unknown"}):
            with self.subTest(location=location):
                self.files["accessPoints.json"] = {"accessPoints": [
                    {"name": "Unplaced AP", "location": location}
                ]}
                ap = self.convert()["accesspoints"][0]
                self.assertEqual(ap["name"], "Unplaced AP")
                self.assertNotIn("coordinates", ap)
                self.assertNotIn("floorplan_name", ap)

    def test_ap_with_known_floor_but_no_coordinates(self):
        self.files["accessPoints.json"] = {"accessPoints": [
            {"name": "AP-1", "location": {"floorPlanId": "floor-1"}}
        ]}
        ap = self.convert()["accesspoints"][0]
        self.assertEqual(ap["floorplan_name"], "Ground")
        self.assertNotIn("coordinates", ap)

    def test_mixed_floors(self):
        self.add_walls()
        self.files["floorPlans.json"]["floorPlans"].append(
            {"id": "floor-2", "name": "Upper", "width": 100, "height": 80})
        floors = self.convert()["floorplans"]
        self.assertEqual(len(floors[0]["wall_segments"]), 1)
        self.assertNotIn("wall_segments", floors[1])

    def test_wall_dependencies_remain_required(self):
        for name in WALL_FILES[1:]:
            with self.subTest(missing=name):
                self.add_walls()
                self.files.pop(name)
                result = self.convert(success=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"Missing required file in ESX: {name}", result.stderr)

    def test_required_floor_data_remains_required(self):
        self.files.pop("floorPlans.json")
        result = self.convert(success=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Missing required file in ESX: floorPlans.json", result.stderr)

    def test_malformed_floor_data_is_not_treated_as_empty(self):
        for invalid in (b"{invalid JSON", {}):
            with self.subTest(invalid=invalid):
                self.files["floorPlans.json"] = invalid
                self.convert(success=False)

    def test_floor_image_remains_required(self):
        self.files.pop("image-floor.png")
        result = self.convert(success=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("No suitable floor image found", result.stderr)

    def test_malformed_present_wall_files_are_not_treated_as_absent(self):
        for name in WALL_FILES:
            for invalid in (b"{invalid JSON", {}):
                with self.subTest(name=name, invalid=invalid):
                    for wall_file in WALL_FILES:
                        self.files.pop(wall_file, None)
                    self.files[name] = invalid
                    self.convert(success=False)


if __name__ == "__main__":
    unittest.main()

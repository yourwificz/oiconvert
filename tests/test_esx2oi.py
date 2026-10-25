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

    def test_ap_notes_and_tags_are_preserved_as_source_metadata(self):
        self.add_planned_ap()
        ap = self.files["accessPoints.json"]["accessPoints"][0]
        ap.update(tags=["Installation", {"label": "Review", "color": "#123456"}],
                  noteIds=["note-b", "note-a"])
        self.files["notes.json"] = {"notes": [
            {"id": "note-a", "text": "Line one\nPříliš žluťoučký 🛠", "imageIds": ["image-a"]},
            {"id": "note-b", "text": "<b>Keep formatting</b>", "status": "CREATED"},
            {"id": "unrelated", "text": "Unrelated floor note"}]}
        result = self.convert()["accesspoints"]
        self.assertEqual(result[0]["oiconvert_source"], {
            "version": 1, "tags": ap["tags"], "note_ids": ["note-b", "note-a"],
            "notes": [{"id": "note-b", "text": "<b>Keep formatting</b>"},
                      {"id": "note-a", "text": "Line one\nPříliš žluťoučký 🛠", "imageIds": ["image-a"]}]})
        self.assertNotIn("notes", result[0])
        self.assertNotIn("tags", result[0])
        self.assertNotIn("oiconvert_source", result[1])

    def test_missing_and_deleted_ap_notes_keep_only_references(self):
        self.add_planned_ap()
        self.files["accessPoints.json"]["accessPoints"][0]["noteIds"] = ["missing", "deleted"]
        for present in (False, True):
            with self.subTest(notes_file_present=present):
                if present:
                    self.files["notes.json"] = {"notes": [
                        {"id": "deleted", "text": "Do not resurrect", "status": "DELETED"}]}
                metadata = self.convert()["accesspoints"][0]["oiconvert_source"]
                self.assertEqual(metadata, {"version": 1, "note_ids": ["missing", "deleted"]})

    def test_empty_ap_metadata_has_no_extension(self):
        self.add_planned_ap()
        self.files["accessPoints.json"]["accessPoints"][0].update(tags=[], noteIds=[])
        self.assertNotIn("oiconvert_source", self.convert()["accesspoints"][0])

    def test_unplaced_ap_metadata_is_preserved(self):
        self.add_planned_ap()
        self.files["accessPoints.json"]["accessPoints"][1]["tags"] = ["Unplaced"]
        ap = self.convert()["accesspoints"][1]
        self.assertEqual(ap["oiconvert_source"]["tags"], ["Unplaced"])
        self.assertNotIn("coordinates", ap)

    def test_invalid_ap_metadata_is_rejected(self):
        for field, value in (("tags", "tag"), ("tags", [float("nan")]),
                             ("noteIds", "note"), ("noteIds", [False])):
            with self.subTest(field=field, value=value):
                self.add_planned_ap()
                self.files["accessPoints.json"]["accessPoints"][0][field] = value
                self.convert(success=False)

    def test_invalid_and_duplicate_referenced_notes_are_rejected(self):
        self.add_planned_ap()
        self.files["accessPoints.json"]["accessPoints"][0]["noteIds"] = ["note"]
        for records in ([{"id": "note", "text": None}],
                        [{"id": "note", "text": "Text", "imageIds": "image"}],
                        [{"id": "note", "text": "A"}, {"id": "note", "text": "B"}]):
            with self.subTest(records=records):
                self.files["notes.json"] = {"notes": records}
                self.convert(success=False)

    def test_explicit_empty_aps(self):
        self.files["accessPoints.json"] = {"accessPoints": []}
        self.assertNotIn("accesspoints", self.convert())

    def add_planned_ap(self):
        self.files["accessPoints.json"] = {"accessPoints": [
            {"id": "planned", "name": "Planned", "vendor": "Example", "model": "Model",
             "color": "#12aBcD", "location": {"floorPlanId": "floor-1",
             "coord": {"x": 50, "y": 10}}},
            {"id": "measured", "name": "Measured"},
        ]}
        self.files["simulatedRadios.json"] = {"simulatedRadios": [
            {"accessPointId": "planned", "accessPointIndex": slot,
             "radioTechnology": "IEEE802_11", "enabled": True,
             "channelByCenterFrequencyDefinedNarrowChannels": frequencies,
             "antennaHeight": 3.1, "transmitPower": 17.5, "spatialStreamCount": 2}
            for slot, frequencies in [(0, [2437]), (1, [5200, 5180]),
                                       (2, [6015, 5995, 5975, 5955])]
        ]}
        return self.files["simulatedRadios.json"]["simulatedRadios"]

    def test_planned_channels_heights_colors_and_no_invented_properties(self):
        self.add_planned_ap()
        ap, measured = self.convert()["accesspoints"]
        self.assertEqual(ap["dot11_radios"], [
            {"id": 0, "radio_function": "CLIENT_ACCESS", "band": "FREQ_2.4GHZ",
             "channel": 6, "channel_width": "20_MHz"},
            {"id": 1, "radio_function": "CLIENT_ACCESS", "band": "FREQ_5GHZ",
             "channel": 36, "channel_width": "40_MHz"},
            {"id": 2, "radio_function": "CLIENT_ACCESS", "band": "FREQ_6GHZ",
             "channel": 1, "channel_width": "80_MHz"},
        ])
        self.assertEqual(ap["display_color"], "#12aBcD")
        for coordinate, expected_z in zip(ap["coordinates"], [31.0, 3.1, 3.1*3.28084]):
            self.assertAlmostEqual(coordinate["coordinate_xyz"]["z"], expected_z)
        self.assertNotIn("orientation", ap)
        self.assertNotIn("dot11_radios", measured)

    def test_disabled_deleted_and_bluetooth_radios(self):
        radios = self.add_planned_ap()
        radios[0]["enabled"] = False
        radios[1]["status"] = "DELETED"
        radios.append({"radioTechnology": "BLUETOOTH", "accessPointId": "planned"})
        output = self.convert()["accesspoints"][0]["dot11_radios"]
        self.assertEqual([r["id"] for r in output], [0, 2])
        self.assertEqual(output[0]["radio_function"], "DISABLED")

    def test_missing_radio_channels_are_not_invented(self):
        for radio in self.add_planned_ap():
            radio.pop("channelByCenterFrequencyDefinedNarrowChannels")
        self.assertEqual(self.convert()["accesspoints"][0]["dot11_radios"],
                         [{"id": i, "radio_function": "CLIENT_ACCESS"} for i in range(3)])

    def test_invalid_radio_spectrum_is_rejected(self):
        for frequencies in ([True], [float("nan")], ["5180"], [123],
                            [5180, 5180], [2412, 5180], [5180, 5260],
                            [5180, 5200, 5220], [2412, 2432, 2452, 2472]):
            with self.subTest(frequencies=frequencies):
                self.add_planned_ap()[0]["channelByCenterFrequencyDefinedNarrowChannels"] = frequencies
                self.convert(success=False)

    def test_radio_association_and_indices_are_validated(self):
        radios = self.add_planned_ap()
        radios[0]["accessPointId"] = "unknown"
        self.assertIn("unknown AP", self.convert(success=False).stderr)
        radios = self.add_planned_ap()
        radios[1]["accessPointIndex"] = 0
        self.assertIn("radio index", self.convert(success=False).stderr)

    def test_invalid_and_conflicting_heights_are_rejected(self):
        for height in (None, True, -1, float("inf"), "3", 4):
            with self.subTest(height=height):
                self.add_planned_ap()[0]["antennaHeight"] = height
                self.convert(success=False)

    def test_zero_installation_height_is_preserved(self):
        for radio in self.add_planned_ap():
            radio["antennaHeight"] = 0
        self.assertTrue(all(c["coordinate_xyz"]["z"] == 0
                            for c in self.convert()["accesspoints"][0]["coordinates"]))

    def test_invalid_ap_color_is_rejected(self):
        self.add_planned_ap()
        self.files["accessPoints.json"]["accessPoints"][0]["color"] = "red"
        self.assertIn("display color", self.convert(success=False).stderr)

    def chain_map_file(self, entries):
        path = Path(self.temp.name) / "radio-chains.json"
        path.write_text(json.dumps(entries))
        return str(path)

    def test_total_to_per_chain_power_uses_explicit_chain_map(self):
        radios = self.add_planned_ap()
        radios[0]["transmitPower"] = 20
        radios[0]["spatialStreamCount"] = 1  # Does not override explicit RF chains.
        path = self.chain_map_file([{"manufacturer": "Example", "model": "Model",
                                     "band": "FREQ_2.4GHZ", "chains": 4}])
        for options in ([], ["--force-fallback-model"]):
            with self.subTest(options=options):
                output = self.convert("--radio-chain-map", path, *options)["accesspoints"][0]["dot11_radios"]
                self.assertEqual(output[0]["transmit_power"], 14)
                self.assertEqual(output[0]["mimo_chains"], 4)
                self.assertNotIn("transmit_power", output[1])

    def test_integrated_antenna_eirp_and_external_antenna_loss_uncertainty(self):
        radios = self.add_planned_ap()
        for r in radios:
            r["antennaTypeId"] = "antenna"
        self.files["antennaTypes.json"] = {"antennaTypes": [
            {"id": "antenna", "maxGain": 4.5, "apCoupling": "INTERNAL_ANTENNA"}]}
        output = self.convert()["accesspoints"][0]["dot11_radios"]
        self.assertTrue(all(r["transmit_eirp"] == 22.0 for r in output))
        self.assertTrue(all("transmit_power" not in r and "mimo_chains" not in r for r in output))
        self.files["antennaTypes.json"]["antennaTypes"][0]["apCoupling"] = "EXTERNAL_ANTENNA"
        self.assertTrue(all("transmit_eirp" not in r for r in
                            self.convert()["accesspoints"][0]["dot11_radios"]))

    def test_power_quantization_and_zero_power(self):
        path = self.chain_map_file([{"manufacturer": "Example", "model": "Model",
                                     "band": "FREQ_2.4GHZ", "chains": 1}])
        for total, expected in [(0, 0), (-2.5, -2), (12.5, 13), (-3, -3)]:
            with self.subTest(total=total):
                self.add_planned_ap()[0]["transmitPower"] = total
                result = self.convert("--radio-chain-map", path)["accesspoints"][0]["dot11_radios"][0]
                self.assertEqual(result["transmit_power"], expected)

    def test_invalid_power_and_gain_are_rejected(self):
        for power in (True, "14", float("nan"), float("inf")):
            with self.subTest(power=power):
                self.add_planned_ap()[0]["transmitPower"] = power
                self.convert(success=False)
        self.add_planned_ap()[0]["antennaTypeId"] = "antenna"
        self.files["antennaTypes.json"] = {"antennaTypes": [
            {"id": "antenna", "maxGain": "4", "apCoupling": "INTERNAL_ANTENNA"}]}
        self.convert(success=False)

    def test_invalid_chain_map_is_rejected(self):
        entry = {"manufacturer": "Example", "model": "Model", "band": "FREQ_5GHZ", "chains": 2}
        for entries in ({}, [None], [entry, entry],
                        [{**entry, "chains": True}], [{**entry, "chains": 0}],
                        [{**entry, "chains": 2.5}], [{**entry, "band": "5GHz"}],
                        [{**entry, "model": ""}]):
            with self.subTest(entries=entries):
                self.convert("--radio-chain-map", self.chain_map_file(entries), success=False)

    def test_radio_channel_edges_and_wide_spectrum(self):
        cases = [([2484], "FREQ_2.4GHZ", 14, "20_MHz"),
                 ([5935], "FREQ_6GHZ", 2, "20_MHz"),
                 ([5180 + 20*i for i in range(8)], "FREQ_5GHZ", 36, "160_MHz"),
                 ([5955 + 20*i for i in range(16)], "FREQ_6GHZ", 1, "320_MHz")]
        for frequencies, band, channel, width in cases:
            with self.subTest(channel=channel, width=width):
                self.add_planned_ap()[0]["channelByCenterFrequencyDefinedNarrowChannels"] = frequencies
                radio = self.convert()["accesspoints"][0]["dot11_radios"][0]
                self.assertEqual((radio["band"], radio["channel"], radio["channel_width"]),
                                 (band, channel, width))

    def test_unscaled_height_is_not_written_as_pixel_height(self):
        self.add_planned_ap()
        self.files["floorPlans.json"]["floorPlans"][0].pop("metersPerUnit")
        coordinates = self.convert()["accesspoints"][0]["coordinates"]
        self.assertEqual(len(coordinates), 1)
        self.assertNotIn("z", coordinates[0]["coordinate_xyz"])

    def test_source_ap_model_preserved_verbatim(self):
        self.files["accessPoints.json"] = {"accessPoints": [
            {"name": "Ruckus AP", "vendor": "Ruckus", "model": "R850"},
            {"name": "Cisco AP", "manufacturer": "Cisco", "model": "C9130AXI-E"},
        ]}
        for ap, expected in zip(self.convert()["accesspoints"],
                                [("Ruckus", "R850"), ("Cisco", "C9130AXI-E")]):
            self.assertEqual((ap["manufacturer"], ap["model"]), expected)
            # An AP model is not an antenna model. Let the importer use its catalog.
            self.assertNotIn("antennas", ap)

    def test_incomplete_ap_identity_uses_whole_fallback_pair(self):
        for identity in ({}, {"vendor": "Cisco"}, {"model": "R850"},
                         {"vendor": "Cisco", "model": "  "}):
            with self.subTest(identity=identity):
                self.files["accessPoints.json"] = {"accessPoints": [{"name": "AP", **identity}]}
                ap = self.convert("--fallback-manufacturer", "Example",
                                  "--fallback-model", "Known-Model")["accesspoints"][0]
                self.assertEqual((ap["manufacturer"], ap["model"]), ("Example", "Known-Model"))
                self.assertEqual(ap["manufacturer_original"], identity.get("vendor", "").strip())
                self.assertEqual(ap["model_original"], identity.get("model", "").strip())

    def test_force_fallback_model(self):
        self.files["accessPoints.json"] = {"accessPoints": [
            {"name": "AP", "vendor": "Ruckus", "model": "R850"}
        ]}
        ap = self.convert("--force-fallback-model")["accesspoints"][0]
        self.assertEqual((ap["manufacturer"], ap["model"]), ("ubiquiti", "uap-ac-pro"))
        self.assertEqual(ap["model_original"], "R850")

    def test_coverage_and_exclusions_use_correct_floor_and_y_origin(self):
        self.files["floorPlans.json"]["floorPlans"].append(
            {"id": "floor-2", "name": "Upper", "width": 100, "height": 80})
        points = [{"x": 10, "y": 20}, {"x": 60, "y": 20}, {"x": 60, "y": 50}]
        self.files["areas.json"] = {"areas": [
            {"floorPlanId": "floor-1", "area": points},
            {"floorPlanId": "floor-2", "area": list(reversed(points))},
            {"floorPlanId": "floor-1", "status": "DELETED"},
        ]}
        self.files["exclusionAreas.json"] = {"exclusionAreas": [
            {"floorPlanId": "floor-1", "area": points}
        ]}
        floors = self.convert()["floorplans"]
        self.assertEqual([len(f["coverage_areas"]) for f in floors], [2, 1])
        included, excluded = floors[0]["coverage_areas"]
        self.assertFalse(included["exclusion"])
        self.assertTrue(excluded["exclusion"])
        expected = [{"coordinate_xyz": {"x": x, "y": y, "z": 0.0, "unit": "pixels"}}
                    for x, y in [(10.0, 60.0), (60.0, 60.0), (60.0, 30.0)]]
        self.assertEqual(included["area"]["coordinates"], expected)
        self.assertEqual(excluded["area"]["coordinates"], expected)
        self.assertEqual(floors[1]["coverage_areas"][0]["area"]["coordinates"], expected[::-1])

    def test_coverage_unknown_floor_is_rejected(self):
        self.files["areas.json"] = {"areas": [{"floorPlanId": "missing", "area": []}]}
        self.assertIn("unknown floor", self.convert(success=False).stderr)

    def test_malformed_coverage_is_rejected(self):
        for points in (None, [], [{"x": 1, "y": 2}] * 2,
                       [{"x": "1", "y": 2}] * 3,
                       [{"x": 1, "y": float("nan")}] * 3,
                       [{"x": True, "y": 2}] * 3):
            with self.subTest(points=points):
                self.files["areas.json"] = {"areas": [{"floorPlanId": "floor-1", "area": points}]}
                self.convert(success=False)

    def test_empty_coverage_and_attenuation_areas_are_not_coverage(self):
        self.files["areas.json"] = {"areas": []}
        self.files["exclusionAreas.json"] = {"exclusionAreas": []}
        self.files["attenuationAreas.json"] = {"attenuationAreas": [
            {"floorPlanId": "floor-1", "area": [{"x": 1, "y": 2}] * 3}
        ]}
        self.assertEqual(self.convert()["floorplans"][0]["coverage_areas"], [])

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

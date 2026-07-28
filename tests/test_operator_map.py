from __future__ import annotations

import unittest

from heatsafe.ui.operator_console.city_map import MAP_METRICS, district_geojson, map_records
from heatsafe.ui.operator_console.geography import load_hanoi_operator_districts
from heatsafe.ui.operator_console.view_models import OperatorAreaView


def area(*, zone_id: str, selected: bool = False, included: bool = False) -> OperatorAreaView:
    return OperatorAreaView(
        zone_id=zone_id,
        name=zone_id.replace("-", " ").title(),
        latitude=21.0,
        longitude=105.8,
        active_drivers=120,
        heat_index_c=42.0,
        heat_state_label="High heat",
        drivers_needing_break_now=15,
        expected_needing_protection_by_label="20 by 13:15",
        expected_needing_protection_count=20,
        recommended_start_label="11:30",
        plan_status_label="Included" if included else "Watch",
        selected=selected,
        included_in_plan=included,
        exposed_2h=45,
        forecast_requests_30m=180,
    )


class OperatorMapTests(unittest.TestCase):
    def test_boundary_asset_matches_the_fixed_ten_area_scope(self):
        geometry = load_hanoi_operator_districts()
        self.assertEqual(geometry["type"], "FeatureCollection")
        self.assertEqual(len(geometry["features"]), 10)
        self.assertEqual(
            {feature["properties"]["zone_id"] for feature in geometry["features"]},
            {
                "ba-dinh", "bac-tu-liem", "cau-giay", "dong-da", "ha-dong",
                "hai-ba-trung", "hoan-kiem", "hoang-mai", "nam-tu-liem", "thanh-xuan",
            },
        )

    def test_map_layers_only_read_already_presented_area_values(self):
        areas = (area(zone_id="hoan-kiem", selected=True, included=True), area(zone_id="ba-dinh"))
        for metric in MAP_METRICS:
            records = map_records(areas, metric=metric)
            self.assertEqual([record["zone_id"] for record in records], ["hoan-kiem", "ba-dinh"])
            self.assertTrue(all(record["metric_label"] for record in records))
        geometry = district_geojson(map_records(areas, metric="Heat index"))
        self.assertEqual(
            {feature["properties"]["zone_id"] for feature in geometry["features"]},
            {"hoan-kiem", "ba-dinh"},
        )

    def test_selected_and_included_outlines_are_visually_prioritized(self):
        selected = map_records((area(zone_id="hoan-kiem", selected=True, included=True),))[0]
        included = map_records((area(zone_id="ba-dinh", included=True),))[0]
        ordinary = map_records((area(zone_id="cau-giay"),))[0]
        self.assertGreater(selected["line_width"], included["line_width"])
        self.assertGreater(included["line_width"], ordinary["line_width"])


if __name__ == "__main__":
    unittest.main()

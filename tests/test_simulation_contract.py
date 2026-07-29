from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google.cloud import bigquery

from heatsafe.bigquery_io import merge_rows
from heatsafe.config import Settings
from heatsafe.ingestion import calculate_heat_index
from heatsafe.simulation import ScenarioValidationError, load_scenario
from infra.provision_gcp import _ensure_table, table_schemas


ROOT = Path(__file__).resolve().parents[1]


class SettingsContractTests(unittest.TestCase):
    def test_current_defaults_are_valid_and_simulation_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.simulation_enabled)
        self.assertEqual(settings.simulation_scenario_version, "hanoi_heatwave_v1")
        self.assertEqual(settings.simulation_tick_minutes, 15)
        self.assertEqual(settings.simulation_lease_seconds, 360)
        self.assertEqual(
            settings.simulation_staging_dataset_path,
            "cohort2track2.heatsafe_sim_staging",
        )
        self.assertIsNone(settings.simulation_model_dataset)
        self.assertFalse(settings.production_bundle_enabled)
        self.assertIsNone(settings.production_bundle_dataset_id)
        self.assertIsNone(settings.production_bundle_run_id)
        self.assertEqual(settings.production_bundle_tick_index, 41)

    def test_external_identifiers_fail_closed(self):
        invalid = {
            "GOOGLE_CLOUD_PROJECT": [
                "", "A-project", "abcd", "project.id", "project`x",
                "project x", "аbcde1",
            ],
            "HEATSAFE_DATASET": [
                "", "Data", "1data", "data-set", "data.x", "data`x",
                "../data", "dữ_liệu",
            ],
            "HEATSAFE_RAW_BUCKET": [
                "", "Abc", "192.168.1.1", "999.999.999.999", "ab..cd",
                "ab.-cd", "ab-.cd",
                "bucket`name", "../bucket",
            ],
            "HEATSAFE_CURRENT_SNAPSHOT_TABLE": [
                "zone_snapshots_current; DROP TABLE x",
                "other_table",
            ],
            "HEATSAFE_MODE": ["stream", "auto "],
            "HEATSAFE_SCENARIO": ["storm", "heatwave "],
            "HEATSAFE_GEMINI_MODEL": ["gemini-latest", "model`x"],
            "HEATSAFE_SIMULATION_SCENARIO_VERSION": [
                "../hanoi_heatwave_v1", "unknown_v1", "Hanoi_v1",
            ],
            "HEATSAFE_SIMULATION_GENERATOR_VERSION": [
                "../stateful-replay-v1", "unknown-v1", "Stateful-v1",
            ],
            "HEATSAFE_SIMULATION_STAGING_DATASET": [
                "", "Data", "1data", "data-set", "../staging",
            ],
            "HEATSAFE_SIMULATION_MODEL_DATASET": [
                "heatsafe_data", "project.Data", "project.dataset.extra",
                "project.dataset`",
            ],
            "HEATSAFE_PRODUCTION_BUNDLE_DATASET": [
                "Data", "1data", "data-set", "../bundle",
            ],
            "HEATSAFE_PRODUCTION_BUNDLE_RUN_ID": [
                "abc", "A" * 32, "g" * 32, "../" + "a" * 32,
            ],
        }
        for variable, values in invalid.items():
            for value in values:
                with self.subTest(variable=variable, value=value):
                    with patch.dict(os.environ, {variable: value}, clear=True):
                        with self.assertRaises(ValueError):
                            Settings.from_env()

    def test_numeric_and_boolean_configuration_is_strict(self):
        cases = {
            "HEATSAFE_ENABLE_AI": "true",
            "HEATSAFE_SIMULATION_ENABLED": "yes",
            "HEATSAFE_LIVE_FRESHNESS_MINUTES": "0",
            "HEATSAFE_SIMULATION_SEED": "-1",
            "HEATSAFE_SIMULATION_TICK_MINUTES": "10",
            "HEATSAFE_SIMULATION_LEASE_SECONDS": "59",
            "HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX": "96",
        }
        for variable, value in cases.items():
            with self.subTest(variable=variable):
                with patch.dict(os.environ, {variable: value}, clear=True):
                    with self.assertRaises(ValueError):
                        Settings.from_env()

    def test_production_bundle_configuration_is_atomic(self):
        valid = {
            "HEATSAFE_PRODUCTION_BUNDLE_DATASET": "heatsafe_bundle_20260729",
            "HEATSAFE_PRODUCTION_BUNDLE_RUN_ID": "a" * 32,
            "HEATSAFE_PRODUCTION_BUNDLE_TICK_INDEX": "41",
        }
        with patch.dict(os.environ, valid, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.production_bundle_enabled)
        self.assertEqual(
            settings.production_bundle_dataset_id,
            "heatsafe_bundle_20260729",
        )
        for missing in (
            "HEATSAFE_PRODUCTION_BUNDLE_DATASET",
            "HEATSAFE_PRODUCTION_BUNDLE_RUN_ID",
        ):
            incomplete = {key: value for key, value in valid.items() if key != missing}
            with self.subTest(missing=missing):
                with patch.dict(os.environ, incomplete, clear=True):
                    with self.assertRaisesRegex(ValueError, "must be set together"):
                        Settings.from_env()


class BigQuerySchemaContractTests(unittest.TestCase):
    NEW_TABLES = {
        "simulation_scenario_locks",
        "simulation_runs",
        "simulation_ticks",
        "driver_simulation_state",
        "order_events",
        "driver_intervention_events",
        "simulation_control_events",
        "simulation_control_consumptions",
    }

    EXTENSIONS = {
        "weather_observations": {
            "simulation_run_id", "tick_id", "source_observed_at",
            "source_next_observed_at", "source_interpolation_fraction",
            "source_temperature_c", "temperature_adjustment_c",
            "station_peak_anchor_c", "apparent_temperature_c", "wind_speed_mps",
            "wind_gust_mps", "precipitation_mm", "cloud_cover_pct",
            "shortwave_radiation_wm2", "utci_c", "derivation_version",
            "generator_version",
        },
        "zone_operations": {
            "simulation_run_id", "tick_id", "online_drivers", "idle_drivers",
            "to_pickup_drivers", "on_trip_drivers", "to_coolstop_drivers",
            "paused_drivers", "exposed_2_to_4h", "requests_15m", "matched_15m",
            "completed_15m", "cancelled_15m", "unfulfilled_15m",
            "median_wait_minutes", "p90_wait_minutes", "fulfillment_rate",
            "generator_version",
        },
        "demand_history": {"simulation_run_id", "tick_id", "generator_version"},
        "driver_state_history": {
            "simulation_run_id", "tick_id", "driver_status", "heat_dose_120m",
            "acclimatization_class", "current_order_id",
            "current_intervention_id", "earnings_60m_vnd",
            "platform_contribution_60m_vnd", "generator_version",
        },
        "driver_current_features": {
            "simulation_run_id", "tick_id", "driver_status", "heat_dose_120m",
            "acclimatization_class", "generator_version",
        },
        "driver_risk_predictions": {
            "simulation_run_id", "tick_id", "generator_version",
        },
        "zone_demand_forecasts": {
            "simulation_run_id", "tick_id", "generator_version",
        },
        "intervention_proposals": {
            "scenario_id", "source_snapshot_id", "simulation_run_id",
            "source_tick_id", "expires_at",
        },
        "intervention_events": {
            "scenario_id", "source_snapshot_id", "simulation_run_id",
            "source_tick_id", "expires_at",
        },
        "zone_snapshots_current": {
            "simulation_run_id", "tick_id", "generator_version",
            "online_drivers", "idle_drivers", "to_pickup_drivers",
            "on_trip_drivers", "to_coolstop_drivers", "paused_drivers",
            "exposed_2_to_4h", "requests_15m", "matched_15m",
            "completed_15m", "cancelled_15m", "unfulfilled_15m",
            "median_wait_minutes", "p90_wait_minutes", "fulfillment_rate",
        },
    }

    def test_eight_simulator_tables_are_present(self):
        self.assertTrue(self.NEW_TABLES <= table_schemas().keys())

    def test_every_existing_table_extension_is_nullable(self):
        schemas = table_schemas()
        for table_name, extension_names in self.EXTENSIONS.items():
            fields = {field.name: field for field in schemas[table_name]}
            with self.subTest(table=table_name):
                self.assertTrue(extension_names <= fields.keys())
                self.assertTrue(
                    all(fields[name].mode == "NULLABLE" for name in extension_names)
                )

    def test_schema_has_unique_names(self):
        for table_name, schema in table_schemas().items():
            with self.subTest(table=table_name):
                names = [field.name for field in schema]
                self.assertEqual(len(names), len(set(names)))

    def test_existing_schema_conflict_fails_before_update(self):
        desired = [
            bigquery.SchemaField("scenario_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("tick_id", "STRING", mode="NULLABLE"),
        ]

        class Client:
            def __init__(self):
                self.table = SimpleNamespace(
                    schema=[
                        bigquery.SchemaField(
                            "scenario_id", "INTEGER", mode="REQUIRED"
                        )
                    ],
                    time_partitioning=None,
                    clustering_fields=[],
                    labels={},
                )
                self.updated = False

            def get_table(self, _table_id):
                return self.table

            def update_table(self, *_args):
                self.updated = True

        client = Client()
        with self.assertRaisesRegex(RuntimeError, "schema conflict"):
            _ensure_table(client, "p.d.t", desired, None, None)
        self.assertFalse(client.updated)

    def test_missing_required_field_on_existing_table_fails_closed(self):
        desired = [
            bigquery.SchemaField("scenario_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("generation", "INTEGER", mode="REQUIRED"),
        ]

        class Client:
            table = SimpleNamespace(
                schema=[
                    bigquery.SchemaField("scenario_id", "STRING", mode="REQUIRED")
                ],
                time_partitioning=None,
                clustering_fields=[],
                labels={},
            )

            def get_table(self, _table_id):
                return self.table

        with self.assertRaisesRegex(RuntimeError, "cannot add REQUIRED"):
            _ensure_table(Client(), "p.d.t", desired, None, None)

    def test_additive_current_migration_preserves_existing_physical_layout(self):
        desired = [
            bigquery.SchemaField("scenario_id", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("tick_id", "STRING", mode="NULLABLE"),
        ]

        class Client:
            def __init__(self):
                self.table = SimpleNamespace(
                    schema=[
                        bigquery.SchemaField(
                            "scenario_id", "STRING", mode="NULLABLE"
                        )
                    ],
                    time_partitioning=None,
                    clustering_fields=["zone_id"],
                    labels={},
                )
                self.updated_fields = []

            def get_table(self, _table_id):
                return self.table

            def update_table(self, _table, fields):
                self.updated_fields = fields

        client = Client()
        _ensure_table(
            client,
            "p.d.t",
            desired,
            None,
            ["scenario_id", "zone_id"],
            preserve_existing_layout=True,
        )
        self.assertEqual(client.table.clustering_fields, ["zone_id"])
        self.assertIn("schema", client.updated_fields)
        self.assertIn("tick_id", {field.name for field in client.table.schema})


class MergePolicyTests(unittest.TestCase):
    class Done:
        def result(self):
            return None

    class Client:
        def __init__(self):
            self.query_text = ""

        def load_table_from_json(self, *_args, **_kwargs):
            return MergePolicyTests.Done()

        def query(self, query, **_kwargs):
            self.query_text = query
            return MergePolicyTests.Done()

        def delete_table(self, *_args, **_kwargs):
            return None

    def test_legacy_merge_does_not_clear_omitted_simulator_lineage(self):
        schema = [
            bigquery.SchemaField("scenario_id", "STRING"),
            bigquery.SchemaField("legacy_value", "INTEGER"),
            bigquery.SchemaField("simulation_run_id", "STRING"),
        ]
        client = self.Client()
        merge_rows(
            client,
            "project.dataset.table_name",
            [{"scenario_id": "live", "legacy_value": 1}],
            schema,
            ["scenario_id"],
        )
        self.assertIn("target.legacy_value = source.legacy_value", client.query_text)
        self.assertNotIn(
            "target.simulation_run_id = source.simulation_run_id",
            client.query_text,
        )

    def test_explicit_current_replacement_can_clear_lineage(self):
        schema = [
            bigquery.SchemaField("scenario_id", "STRING"),
            bigquery.SchemaField("legacy_value", "INTEGER"),
            bigquery.SchemaField("simulation_run_id", "STRING"),
        ]
        client = self.Client()
        merge_rows(
            client,
            "project.dataset.table_name",
            [{"scenario_id": "live", "legacy_value": 1}],
            schema,
            ["scenario_id"],
            update_fields=["legacy_value", "simulation_run_id"],
        )
        self.assertIn(
            "target.simulation_run_id = source.simulation_run_id",
            client.query_text,
        )


class ScenarioFixtureTests(unittest.TestCase):
    def test_reviewed_fixture_loads_and_preserves_provenance(self):
        fixture = load_scenario("hanoi_heatwave_v1")
        self.assertEqual(len(fixture.weather), 96)
        self.assertEqual(
            fixture.realism_profile["classification"], "synthetic-prior"
        )
        peak = max(fixture.weather, key=lambda row: row["temperature_c"])
        self.assertEqual(peak["local_time"].isoformat(), "2026-05-26T16:00:00+07:00")
        self.assertAlmostEqual(peak["temperature_c"], 41.1)
        self.assertAlmostEqual(peak["relative_humidity_percent"], 44)
        self.assertAlmostEqual(
            calculate_heat_index(
                peak["temperature_c"], peak["relative_humidity_percent"]
            ),
            53.9,
        )
        self.assertTrue(
            all(
                row["temperature_c"] >= row["source_temperature_c"]
                for row in fixture.weather
            )
        )

    def test_unknown_or_traversal_scenario_is_rejected(self):
        for version in ("unknown_v1", "../hanoi_heatwave_v1", "Hanoi_v1"):
            with self.subTest(version=version):
                with self.assertRaises(ScenarioValidationError):
                    load_scenario(version)

    def test_fixture_is_included_in_container_build_context(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("COPY . .", dockerfile)
        self.assertNotIn("data/scenarios", dockerignore)


class SchemaOnlyCliTests(unittest.TestCase):
    def test_schema_only_never_calls_bucket_provisioning(self):
        from infra import provision_gcp

        fake_client = object()
        argv = [
            "provision_gcp.py",
            "--schema-only",
            "--dataset",
            "heatsafe_phase1_contract_test",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(provision_gcp, "ensure_bucket") as ensure_bucket,
            patch.object(
                provision_gcp, "ensure_bigquery", return_value=fake_client
            ) as ensure_bigquery,
            patch.object(provision_gcp, "print_schema_readback") as readback,
            patch.dict(os.environ, {}, clear=True),
        ):
            provision_gcp.main()
        ensure_bucket.assert_not_called()
        ensure_bigquery.assert_called_once()
        readback.assert_called_once()

    def test_schema_only_current_is_explicit_and_never_calls_bucket(self):
        from infra import provision_gcp

        fake_client = object()
        argv = ["provision_gcp.py", "--schema-only-current"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(provision_gcp, "ensure_bucket") as ensure_bucket,
            patch.object(
                provision_gcp, "ensure_bigquery", return_value=fake_client
            ) as ensure_bigquery,
            patch.object(provision_gcp, "print_schema_readback") as readback,
            patch.dict(os.environ, {}, clear=True),
        ):
            provision_gcp.main()
        ensure_bucket.assert_not_called()
        ensure_bigquery.assert_called_once_with(
            Settings.from_env(), preserve_existing_layout=True
        )
        readback.assert_not_called()


if __name__ == "__main__":
    unittest.main()

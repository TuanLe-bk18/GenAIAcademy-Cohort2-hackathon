from __future__ import annotations

from dataclasses import replace
from datetime import UTC
import base64
import gzip
import io
import json
import math
import unittest
import google_crc32c

from heatsafe.simulation.checkpoint import (
    CheckpointConflict,
    CheckpointError,
    InMemoryCheckpointStore,
    GCSCheckpointStore,
    checkpoint_object_name,
    decode_checkpoint,
    encode_checkpoint,
)
from heatsafe.simulation.models import PauseControl
from heatsafe.simulation.repository import InMemorySimulationRepository
from heatsafe.simulation.telemetry import TickTelemetry
from heatsafe.simulation.engine import (
    advance_tick,
    initialize_state,
    load_scenario,
    load_zone_priors,
)


class CheckpointCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_scenario("hanoi_heatwave_v1")
        cls.zones = load_zone_priors()

    def _state_at(self, tick_index: int):
        state = initialize_state(seed=42, fixture=self.fixture, zones=self.zones)
        for _ in range(tick_index + 1):
            state = advance_tick(
                state, fixture=self.fixture, zones=self.zones
            ).state
        return state

    def test_round_trip_is_byte_stable_and_preserves_next_transition(self):
        for tick_index in (0, 24, 48, 95):
            with self.subTest(tick=tick_index):
                state = self._state_at(tick_index)
                first = encode_checkpoint(state)
                second = encode_checkpoint(state)
                restored = decode_checkpoint(
                    first.data,
                    expected_payload_sha256=first.payload_sha256,
                    expected_state_checksum=first.state_checksum,
                )
                self.assertEqual(first.data, second.data)
                self.assertEqual(state, restored)
                self.assertEqual(state.start_time.utcoffset().total_seconds(), 25_200)
                if tick_index < 95:
                    expected = advance_tick(
                        state, fixture=self.fixture, zones=self.zones
                    )
                    actual = advance_tick(
                        restored, fixture=self.fixture, zones=self.zones
                    )
                    self.assertEqual(expected.checksum, actual.checksum)

    def test_data_only_decoder_fails_closed_for_invalid_documents(self):
        state = self._state_at(0)
        encoded = encode_checkpoint(state)

        def rewrite(mutator):
            raw = gzip.decompress(encoded.data)
            document = json.loads(raw)
            mutator(document)
            output = io.BytesIO()
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=output, mtime=0
            ) as target:
                target.write(
                    json.dumps(
                        document, sort_keys=True, separators=(",", ":")
                    ).encode()
                )
            return output.getvalue()

        cases = [
            lambda doc: doc.update({"unknown": True}),
            lambda doc: doc.update({"format_version": "unknown-v9"}),
            lambda doc: doc["state"]["fields"].pop("minute_index"),
            lambda doc: doc["state"]["fields"]["drivers"]["$tuple"][0][
                "fields"
            ]["status"].update({"value": "FLYING"}),
            lambda doc: doc["state"]["fields"]["start_time"].update(
                {"$datetime_utc": "not-a-date"}
            ),
        ]
        for index, mutator in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(CheckpointError):
                decode_checkpoint(rewrite(mutator))
        with self.assertRaises(CheckpointError):
            decode_checkpoint(b"not gzip")
        with self.assertRaisesRegex(CheckpointError, "payload hash"):
            decode_checkpoint(
                encoded.data, expected_payload_sha256="0" * 64
            )
        with self.assertRaisesRegex(CheckpointError, "metadata mismatch"):
            decode_checkpoint(
                encoded.data, expected_state_checksum="0" * 64
            )

    def test_non_finite_float_is_rejected(self):
        state = self._state_at(0)
        driver = replace(state.drivers[0], latitude=math.inf)
        with self.assertRaisesRegex(CheckpointError, "finite"):
            encode_checkpoint(replace(state, drivers=(driver, *state.drivers[1:])))

    def test_store_precondition_accepts_identical_and_rejects_different(self):
        store = InMemoryCheckpointStore()
        first = encode_checkpoint(self._state_at(0))
        name = checkpoint_object_name(
            run_id="run-1", tick_index=0, input_checksum="a" * 64
        )
        metadata = store.put(name, first)
        self.assertEqual(metadata, store.put(name, first))
        with self.assertRaises(CheckpointConflict):
            store.put(name, encode_checkpoint(self._state_at(1)))

    def test_datetime_instant_is_canonical_utc_but_source_offset_survives(self):
        state = self._state_at(0)
        encoded = encode_checkpoint(state)
        raw = gzip.decompress(encoded.data)
        self.assertIn(b"+00:00", raw)
        self.assertIn(b'"$offset_minutes":"420"', raw)
        restored = decode_checkpoint(encoded.data)
        self.assertEqual(restored.start_time.tzinfo.utcoffset(None), state.start_time.utcoffset())

    def test_gcs_put_uses_upload_checksum_and_metadata_without_full_download(self):
        class Blob:
            generation = None
            size = None
            crc32c = None

            def __init__(self):
                self.upload_checksum = None
                self.download_count = 0

            def upload_from_string(self, data, **kwargs):
                self.upload_checksum = kwargs["checksum"]
                self.generation = 7
                self.size = len(data)
                self.crc32c = base64.b64encode(
                    google_crc32c.Checksum(data).digest()
                ).decode("ascii")

            def reload(self, **_kwargs):
                raise AssertionError("complete upload metadata must not reload")

            def download_as_bytes(self, **_kwargs):
                self.download_count += 1
                raise AssertionError("new checkpoint put must not download")

        blob = Blob()

        class Bucket:
            def blob(self, _name):
                return blob

        class Client:
            def bucket(self, _name):
                return Bucket()

        checkpoint = encode_checkpoint(self._state_at(0))
        metadata = GCSCheckpointStore(Client(), "bucket").put(
            "runs/run/ticks/000/checkpoint", checkpoint
        )
        self.assertEqual(blob.upload_checksum, "crc32c")
        self.assertEqual(blob.download_count, 0)
        self.assertEqual(metadata.generation, 7)
        self.assertEqual(metadata.compressed_size, len(checkpoint.data))

    def test_gcs_put_fails_closed_on_crc32c_metadata_mismatch(self):
        class Blob:
            generation = 1
            size = 1
            crc32c = "invalid"

            def upload_from_string(self, data, **_kwargs):
                self.size = len(data)

        class Client:
            def bucket(self, _name):
                return type(
                    "Bucket",
                    (),
                    {"blob": lambda _self, _name: Blob()},
                )()

        checkpoint = encode_checkpoint(self._state_at(0))
        with self.assertRaisesRegex(CheckpointError, "CRC32C"):
            GCSCheckpointStore(Client(), "bucket").put(
                "runs/run/ticks/000/checkpoint", checkpoint
            )


class IncrementalCheckpointRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryCheckpointStore()
        self.repository = InMemorySimulationRepository(
            checkpoint_store=self.store, state_mode="checkpoint"
        )
        self.run = self.repository.start(
            scenario_id="heatwave",
            scenario_version="hanoi_heatwave_v1",
            seed=42,
        )

    def _tick(self, index: int):
        tick = next(
            item
            for item in self.repository.ticks.values()
            if item.run_id == self.run.run_id and item.tick_index == index
        )
        lease = self.repository.acquire_tick_lease(
            self.run.run_id, tick.tick_id, f"owner-{index}"
        )
        publication = self.repository.publish_tick(
            self.run.run_id, tick.tick_id, lease.fencing_token
        )
        self.repository.finalize_score(
            self.run.run_id, tick.tick_id, succeeded=True
        )
        return publication, self.repository.ticks[tick.tick_id]

    def test_tick_one_restores_committed_tick_zero_checkpoint(self):
        first, first_tick = self._tick(0)
        second, second_tick = self._tick(1)
        self.assertIsNotNone(first_tick.checkpoint_object_name)
        self.assertIsNotNone(second_tick.checkpoint_object_name)
        expected_state = advance_tick(
            first.result.state,
            fixture=load_scenario("hanoi_heatwave_v1"),
            zones=load_zone_priors(),
        ).state
        self.assertEqual(second.result.state, expected_state)

    def test_missing_latest_checkpoint_falls_back_to_oracle(self):
        _, first_tick = self._tick(0)
        self.store.objects.pop(first_tick.checkpoint_object_name)
        lines: list[str] = []
        telemetry = TickTelemetry(sink=lines.append, state_mode="checkpoint")
        with telemetry.activate():
            second, _ = self._tick(1)
        oracle = self._state_at_oracle(1)
        self.assertEqual(second.result.state, oracle)
        self.assertTrue(
            any(
                json.loads(line).get("error_code") == "CHECKPOINT_FALLBACK"
                for line in lines
            )
        )

    def test_frozen_manifest_is_reused_after_later_control_arrives(self):
        tick = next(
            item
            for item in self.repository.ticks.values()
            if item.tick_index == 0
        )
        lease = self.repository.acquire_tick_lease(
            self.run.run_id, tick.tick_id, "owner"
        )
        frozen = self.repository.freeze_tick_inputs(
            self.run, self.repository.ticks[tick.tick_id]
        )
        self.repository.queue_controls(
            (
                PauseControl(
                    control_id="late-control",
                    driver_ids=("driver",),
                    requested_minute=0,
                    pause_duration_minutes=15,
                ),
            )
        )
        retry = self.repository.freeze_tick_inputs(
            self.run, self.repository.ticks[tick.tick_id]
        )
        self.assertEqual(frozen, retry)
        self.assertEqual(retry.controls, ())
        publication = self.repository.publish_tick(
            self.run.run_id, tick.tick_id, lease.fencing_token
        )
        self.assertNotIn(
            "late-control",
            {item.control_id for item in publication.result.state.interventions},
        )

    def test_fallback_replays_each_historical_frozen_manifest(self):
        first, _ = self._tick(0)
        driver_id = first.result.state.drivers[0].driver_id_hash
        first_control = PauseControl(
            control_id="control-at-tick-1",
            driver_ids=(driver_id,),
            requested_minute=15,
            pause_duration_minutes=15,
        )
        self.repository.queue_controls((first_control,))
        second, second_tick = self._tick(1)
        second_control = PauseControl(
            control_id="control-at-tick-2",
            driver_ids=(driver_id,),
            requested_minute=30,
            pause_duration_minutes=15,
        )
        self.repository.queue_controls((second_control,))
        assert second_tick.checkpoint_object_name is not None
        self.store.objects.pop(second_tick.checkpoint_object_name)

        third, _ = self._tick(2)
        fixture = load_scenario("hanoi_heatwave_v1")
        zones = load_zone_priors()
        expected_second = advance_tick(
            first.result.state,
            fixture=fixture,
            zones=zones,
            controls=(first_control,),
        ).state
        self.assertEqual(second.result.state, expected_second)
        expected_third = advance_tick(
            expected_second,
            fixture=fixture,
            zones=zones,
            controls=(first_control, second_control),
        ).state
        self.assertEqual(third.result.state, expected_third)

    def _state_at_oracle(self, index: int):
        fixture = load_scenario("hanoi_heatwave_v1")
        zones = load_zone_priors()
        state = initialize_state(seed=42, fixture=fixture, zones=zones)
        for _ in range(index + 1):
            state = advance_tick(state, fixture=fixture, zones=zones).state
        return state


if __name__ == "__main__":
    unittest.main()

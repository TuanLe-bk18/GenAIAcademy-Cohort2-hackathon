#!/usr/bin/env python3
"""Build the reviewed accelerated Production-window assets locally."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heatsafe.production_mode import (
    DEFAULT_WINDOW_DIRECTORY,
    build_window_manifest,
    find_production_window,
    state_before_tick,
    write_window_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_WINDOW_DIRECTORY)
    args = parser.parse_args()
    candidate = find_production_window(
        seeds=(42,),
        search_start_tick=40,
        search_end_tick=48,
    )
    if (
        candidate.seed != 42
        or candidate.tick_index != 45
        or candidate.selected_zone_ids
        != ("hai-ba-trung", "cau-giay", "ha-dong")
    ):
        raise RuntimeError("reviewed Production window drifted; stop for review")
    start_tick = candidate.tick_index - 8
    warm_state = state_before_tick(seed=candidate.seed, tick_index=start_tick)
    window = build_window_manifest(candidate, warm_state=warm_state)
    write_window_artifact(window, warm_state, directory=args.output_dir)
    print(window.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

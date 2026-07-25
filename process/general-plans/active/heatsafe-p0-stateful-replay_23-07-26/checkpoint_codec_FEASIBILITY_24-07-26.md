---
slug: checkpoint-codec
date: 24-07-26
verdict: VIABLE
originating-phase: pvl
---

# Feasibility Verdict — Can a schema-explicit JSON/gzip checkpoint preserve the next deterministic transition?

## Hypothesis

A versioned, non-pickle checkpoint can round-trip every `SimulationState` field,
produce deterministic compressed bytes, remain below the frozen byte ceilings,
and preserve the following tick exactly.

## Mechanism Under Test

Explicit dataclass field mappings, float hex tags, tagged bitmask integers,
timezone-aware datetime encoding, canonical JSON, deterministic gzip level 6
with `mtime=0`, typed decoding, count/byte ceilings, and next-tick checksum.

## Probe Family

2 — local runtime/library serialization probe.

## Probe Cost Class

`cheap-local`. No provider resource or active data was used.

## Probe Method

The probe replayed the authoritative fixture to ticks 0/24/48/95. At each
sentinel it encoded, decoded, compared typed state, repeated encoding for byte
identity, and advanced both original/restored states once where another tick
exists. It compared the frozen UTC-only codec to an alternative that stores a
canonical UTC instant plus the original bounded offset.

## Evidence Captured

The UTC-only candidate had typed equality and deterministic bytes at tick 0,
but its next-tick checksum differed: restoring `2026-05-26T00:00+07:00` as the
equal UTC instant changes `.hour`, which the demand engine consumes.

`json-floathex-offset-gzip-v1` passed all applicable checks:

| Tick | Expanded | Compressed | Encode | Decode | Next tick |
|---:|---:|---:|---:|---:|---|
| 0 | 8,097,173 B | 486,790 B | 335.824ms | 313.188ms | equal |
| 24 | 17,930,936 B | 872,791 B | 849.462ms | 741.047ms | equal |
| 48 | 18,285,149 B | 1,036,283 B | 874.445ms | 761.713ms | equal |
| 95 | 17,426,878 B | 800,722 B | 826.796ms | 735.115ms | terminal |

## Verdict

VIABLE

## Resulting Design Constraint

- **What this licenses:** Implement Stage 1 with
  `json-floathex-offset-gzip-v1`, the pinned runtime contract, exact typed
  constructors, deterministic gzip, and frozen decoder ceilings.
- **What this forbids:** Do not use the rejected UTC-only codec, `.6f`
  canonical JSON as checkpoint bytes, pickle, implicit object hooks, or a
  decoder that discards datetime offset semantics.
- **What remains uncertain (known-gap):** GCS create-only generation/hash
  readback, upload/readback p95, corruption recovery, and runtime IAM remain
  Stage 1/5 provider gates.

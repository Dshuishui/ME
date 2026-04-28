"""
test_equivalence_s1.py
======================
Verify that prototype_refactored_s1.py produces identical hardware state
to prototype_raw.py for the same sequence of operations.

This constitutes the validation step of the pipeline:
  LLM identifies modules → modules extracted → refactored code passes tests
  → extraction is correct

Each test:
  1. Resets both modules' hardware state to a fresh HardwareState()
  2. Calls the same operation on both versions
  3. Asserts that the resulting hw state is identical

Run:
  python3 test_equivalence_s1.py
"""

import dataclasses
import traceback

import prototype_raw       as raw
import prototype_refactored_s1 as ref


# ── Helpers ───────────────────────────────────────────────────────────────────

def reset() -> None:
    """Reset both modules to a clean hardware state."""
    raw.hw   = raw.HardwareState()
    raw.ctrl = raw.MachineControl()
    ref.hw   = ref.HardwareState()
    ref.ctrl = ref.MachineControl()


def hw_snapshot(module) -> dict:
    return dataclasses.asdict(module.hw)


def assert_hw_equal(label: str) -> None:
    raw_state = hw_snapshot(raw)
    ref_state = hw_snapshot(ref)
    if raw_state != ref_state:
        diffs = {k: (raw_state[k], ref_state[k])
                 for k in raw_state if raw_state[k] != ref_state[k]}
        raise AssertionError(
            f"[{label}] Hardware state mismatch:\n"
            + "\n".join(f"  {k}: raw={v[0]}  ref={v[1]}" for k, v in diffs.items())
        )


# ── Test cases ────────────────────────────────────────────────────────────────

TESTS: list[tuple[str, callable]] = []

def test(name):
    def decorator(fn):
        TESTS.append((name, fn))
        return fn
    return decorator


@test("aspirate_from_well — basic")
def _():
    reset()
    raw.aspirate_from_well(well_pos=3, volume_ul=20.0)
    ref.aspirate_from_well(well_pos=3, volume_ul=20.0)
    assert_hw_equal("aspirate well=3 vol=20")


@test("aspirate_from_well — different well and volume")
def _():
    reset()
    raw.aspirate_from_well(well_pos=15, volume_ul=50.0)
    ref.aspirate_from_well(well_pos=15, volume_ul=50.0)
    assert_hw_equal("aspirate well=15 vol=50")


@test("dispense_to_well — basic")
def _():
    reset()
    raw.dispense_to_well(well_pos=10, volume_ul=20.0)
    ref.dispense_to_well(well_pos=10, volume_ul=20.0)
    assert_hw_equal("dispense well=10 vol=20")


@test("aspirate then dispense sequence")
def _():
    reset()
    raw.aspirate_from_well(well_pos=3,  volume_ul=30.0)
    raw.dispense_to_well(well_pos=10, volume_ul=30.0)
    ref.aspirate_from_well(well_pos=3,  volume_ul=30.0)
    ref.dispense_to_well(well_pos=10, volume_ul=30.0)
    assert_hw_equal("aspirate+dispense sequence")


@test("wash_tips — 3 cycles")
def _():
    reset()
    raw.wash_tips(wash_pos=0, wash_volume_ul=50.0, cycles=3)
    ref.wash_tips(wash_pos=0, wash_volume_ul=50.0, cycles=3)
    assert_hw_equal("wash 3 cycles")


@test("wash_tips — 1 cycle")
def _():
    reset()
    raw.wash_tips(wash_pos=5, wash_volume_ul=25.0, cycles=1)
    ref.wash_tips(wash_pos=5, wash_volume_ul=25.0, cycles=1)
    assert_hw_equal("wash 1 cycle")


@test("paused system — no state change expected")
def _():
    reset()
    raw.ctrl.autorun  = False
    ref.ctrl.autorun  = False
    raw.aspirate_from_well(well_pos=3, volume_ul=20.0)
    ref.aspirate_from_well(well_pos=3, volume_ul=20.0)
    assert_hw_equal("aspirate while paused")

    # Both should be in the same (unchanged) initial state
    initial = dataclasses.asdict(raw.HardwareState())
    assert hw_snapshot(raw) == initial, "raw: hw changed despite autorun=False"
    assert hw_snapshot(ref) == initial, "ref: hw changed despite autorun=False"


@test("z_actual returns to SAFE_Z after each operation")
def _():
    reset()
    raw.aspirate_from_well(well_pos=7, volume_ul=10.0)
    ref.aspirate_from_well(well_pos=7, volume_ul=10.0)
    assert raw.hw.z_actual == raw.SAFE_Z, f"raw: z_actual={raw.hw.z_actual}"
    assert ref.hw.z_actual == ref.SAFE_Z, f"ref: z_actual={ref.hw.z_actual}"
    assert_hw_equal("z_actual after aspirate")


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> None:
    passed = 0
    failed = 0

    print(f"Running {len(TESTS)} equivalence tests\n")
    print("-" * 50)

    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}")
            print(f"        {e}")
            failed += 1

    print("-" * 50)
    print(f"\n{passed} passed, {failed} failed")

    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

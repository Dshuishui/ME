"""
prototype_refactored_s1.py
==========================
Post-modularization version of prototype_raw.py.

Atomic modules extracted per LLM analysis (Exp01, S1-AST strategy):
  move_xy_to(target_pos)          — Layer 1: XY gantry motion
  move_z_to(z_position)           — Layer 1: Z axis motion
  do_aspirate(volume_ul)          — Layer 1: aspirate pump action
  do_dispense(volume_ul)          — Layer 1: dispense pump action
  well_operation(pos, vol, action)— Layer 2: generic scaffold (XY→Z↓→action→Z↑)

Public interface (aspirate_from_well / dispense_to_well / wash_tips) is
identical to prototype_raw.py — only the internal implementation changes.
"""

from dataclasses import dataclass
from typing import Callable


# ── Hardware state (identical to prototype_raw.py) ───────────────────────────

@dataclass
class HardwareState:
    z_location: int = 0
    z_trigger: bool = False
    z_done: bool = False
    z_actual: int = 0
    z_motor_stable: bool = True
    xy_target: int = 0
    xy_trigger: bool = False
    xy_done: bool = False
    adp_draw_volume: int = 0
    adp_draw_trigger: bool = False
    adp_draw_done: bool = False
    adp_arrange_volume: int = 0
    adp_arrange_trigger: bool = False
    adp_arrange_done: bool = False


@dataclass
class MachineControl:
    autorun: bool = True
    estop_ok: bool = True


hw   = HardwareState()
ctrl = MachineControl()

SAFE_Z = 50_000


# ── Hardware simulator (identical to prototype_raw.py) ───────────────────────

def _hw_tick() -> None:
    if hw.z_trigger and not hw.z_done:
        hw.z_actual = hw.z_location
        hw.z_done = True
        hw.z_motor_stable = True
    if hw.xy_trigger and not hw.xy_done:
        hw.xy_done = True
    if hw.adp_draw_trigger and not hw.adp_draw_done:
        hw.adp_draw_done = True
    if hw.adp_arrange_trigger and not hw.adp_arrange_done:
        hw.adp_arrange_done = True


_WELL_Z_DEPTH: dict[int, int] = {i: 80_000 + i * 500 for i in range(24)}

def _well_z(well_pos: int) -> int:
    return _WELL_Z_DEPTH.get(well_pos, 80_000)


# ── Layer 1: atomic hardware modules ─────────────────────────────────────────

def move_xy_to(target_pos: int) -> None:
    """Move XY gantry to target_pos and wait for completion."""
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    hw.xy_trigger = False
    hw.xy_done = False
    _hw_tick()
    assert not hw.xy_trigger and not hw.xy_done

    hw.xy_target  = target_pos
    hw.xy_trigger = True
    _hw_tick()
    assert hw.xy_done, "XY positioning failed"


def move_z_to(z_position: int) -> None:
    """Move Z axis to z_position and wait until motor is stable."""
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    hw.z_trigger = False
    hw.z_done    = False
    _hw_tick()
    assert not hw.z_trigger and not hw.z_done

    hw.z_location = z_position
    hw.z_trigger  = True
    _hw_tick()
    assert hw.z_done and hw.z_motor_stable, "Z positioning failed"
    hw.z_trigger = False


def do_aspirate(volume_ul: float) -> None:
    """Trigger ADP aspirate pump and wait for completion."""
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    hw.adp_draw_trigger = False
    hw.adp_draw_done    = False
    _hw_tick()
    assert not hw.adp_draw_trigger and not hw.adp_draw_done

    hw.adp_draw_volume  = int(volume_ul * 10)
    hw.adp_draw_trigger = True
    _hw_tick()
    assert hw.adp_draw_done, "ADP aspirate failed"
    hw.adp_draw_trigger = False


def do_dispense(volume_ul: float) -> None:
    """Trigger ADP dispense pump and wait for completion."""
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    hw.adp_arrange_volume  = 0
    hw.adp_arrange_trigger = False
    hw.adp_arrange_done    = False
    _hw_tick()
    assert (not hw.adp_arrange_trigger
            and not hw.adp_arrange_done
            and hw.adp_arrange_volume == 0)

    hw.adp_arrange_volume  = int(volume_ul * 10)
    hw.adp_arrange_trigger = True
    _hw_tick()
    assert hw.adp_arrange_done, "ADP dispense failed"
    hw.adp_arrange_trigger = False


# ── Layer 2: operation scaffold ───────────────────────────────────────────────

def well_operation(well_pos: int, volume_ul: float,
                   fluid_action: Callable[[float], None]) -> None:
    """
    Generic scaffold: move XY to well → lower Z → execute fluid action → raise Z.
    Replaces the duplicated structure in aspirate_from_well and dispense_to_well.
    """
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    move_xy_to(well_pos)
    move_z_to(_well_z(well_pos))
    fluid_action(volume_ul)
    move_z_to(SAFE_Z)


# ── Layer 2: public API (same signature as prototype_raw.py) ─────────────────

def aspirate_from_well(well_pos: int, volume_ul: float) -> None:
    """Full aspirate sequence: move XY → lower Z → aspirate → raise Z."""
    well_operation(well_pos, volume_ul, do_aspirate)


def dispense_to_well(well_pos: int, volume_ul: float) -> None:
    """Full dispense sequence: move XY → lower Z → dispense → raise Z."""
    well_operation(well_pos, volume_ul, do_dispense)


# ── Layer 3: orchestration (unchanged) ───────────────────────────────────────

def wash_tips(wash_pos: int, wash_volume_ul: float, cycles: int = 3) -> None:
    """Wash cycle: alternating aspirate + dispense at wash station."""
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    for _ in range(cycles):
        aspirate_from_well(wash_pos, wash_volume_ul)
        dispense_to_well(wash_pos, wash_volume_ul)


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    aspirate_from_well(well_pos=3, volume_ul=20.0)
    print("aspirate OK, Z =", hw.z_actual)

    dispense_to_well(well_pos=10, volume_ul=20.0)
    print("dispense OK, Z =", hw.z_actual)

    wash_tips(wash_pos=0, wash_volume_ul=50.0, cycles=2)
    print("wash OK")

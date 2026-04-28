"""
prototype_raw.py
================
Pre-modularization prototype for an automated liquid-handling workstation
(domain: DNA synthesis / pipetting robot).

This file intentionally inlines every hardware trigger sequence rather than
extracting common logic into helper functions — mirroring the copy-paste
redundancy found in the original PLC XML before atomic-module extraction.

Hardware model
--------------
  HardwareState  : global hardware registers (≈ Pipette_NOmaintain + Pipette_gun)
  MachineControl : global run/stop signals  (≈ G_MasterControl)

Redundancy patterns embedded here
----------------------------------
  A. Copy-paste trigger sequence
       The 4-step "clear → wait-clear → write-value+fire → wait-done" pattern
       for Z-axis positioning appears 4 times (aspirate↓, aspirate↑,
       dispense↓, dispense↑) with identical code.
       Same pattern for XY positioning appears twice (aspirate, dispense).

  B. Global-state coupling
       aspirate_from_well and dispense_to_well both read/write the same
       hw.adp_draw_done flag to coordinate pump state — the coupling is
       implicit through shared globals, not through explicit parameters.

  C. Multi-layer orchestration
       wash_tips() orchestrates aspirate_from_well + dispense_to_well,
       which in turn orchestrate Z + XY + ADP pump steps — forming the
       same Layer-1 / Layer-2 / Layer-3 hierarchy as the real PLC code.
"""

from dataclasses import dataclass


# ── Global hardware state ─────────────────────────────────────────────────────
# Simulates the PLC hardware global variables (written by FBs, read by POUs).

@dataclass
class HardwareState:
    # Z-axis (≈ Pipette_NOmaintain.Zaxis_*)
    z_location: int = 0
    z_trigger: bool = False
    z_done: bool = False
    z_actual: int = 0
    z_motor_stable: bool = True

    # XY gantry (≈ Pipette_NOmaintain.XY_*)
    xy_target: int = 0
    xy_trigger: bool = False
    xy_done: bool = False

    # ADP aspirate pump (≈ Pipette_NOmaintain.ADP_draw*)
    adp_draw_volume: int = 0
    adp_draw_trigger: bool = False
    adp_draw_done: bool = False

    # ADP dispense pump (≈ Pipette_NOmaintain.ADP_arrange*)
    adp_arrange_volume: int = 0
    adp_arrange_trigger: bool = False
    adp_arrange_done: bool = False


@dataclass
class MachineControl:
    # ≈ G_MasterControl
    autorun: bool = True    # M_Show_Autorun  (True = running, False = paused)
    estop_ok: bool = True   # NOT IN_Trigger_EStop (True = no e-stop)


hw = HardwareState()
ctrl = MachineControl()

SAFE_Z = 50_000   # safe travel height (pulses), ≈ Pipette_maintain.M_ZPT[0]


# ── Hardware simulator ────────────────────────────────────────────────────────
# In the real PLC, hardware FBs run every scan cycle and set done flags.
# Here we call _hw_tick() to simulate one scan cycle completing.

def _hw_tick():
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


# ── Well geometry ─────────────────────────────────────────────────────────────

_WELL_Z_DEPTH: dict[int, int] = {i: 80_000 + i * 500 for i in range(24)}

def _well_z(well_pos: int) -> int:
    return _WELL_Z_DEPTH.get(well_pos, 80_000)


# ── Operations (pre-modularization — trigger sequences inlined) ───────────────

def aspirate_from_well(well_pos: int, volume_ul: float) -> None:
    """
    Full aspirate sequence: move XY → lower Z → aspirate → raise Z.
    """
    if not ctrl.autorun or not ctrl.estop_ok:
        return

    # Step 1 — XY positioning  [Pattern A: trigger sequence #1]
    hw.xy_trigger = False
    hw.xy_done = False
    _hw_tick()
    assert not hw.xy_trigger and not hw.xy_done

    hw.xy_target = well_pos
    hw.xy_trigger = True
    _hw_tick()
    assert hw.xy_done, "XY positioning failed"

    # Step 2 — Z lower to well  [Pattern A: trigger sequence #2]
    hw.z_trigger = False
    hw.z_done = False
    _hw_tick()
    assert not hw.z_trigger and not hw.z_done

    hw.z_location = _well_z(well_pos)
    hw.z_trigger = True
    _hw_tick()
    assert hw.z_done and hw.z_motor_stable, "Z lower failed"

    # Step 3 — ADP aspirate  [Pattern B: touches shared adp_draw_done]
    hw.adp_draw_trigger = False
    hw.adp_draw_done = False
    _hw_tick()
    assert not hw.adp_draw_trigger and not hw.adp_draw_done

    hw.adp_draw_volume = int(volume_ul * 10)   # unit: 0.1 µL
    hw.adp_draw_trigger = True
    _hw_tick()
    assert hw.adp_draw_done, "ADP aspirate failed"
    hw.adp_draw_trigger = False

    # Step 4 — Z raise to safe height  [Pattern A: trigger sequence #3 — same as #2]
    hw.z_trigger = False
    hw.z_done = False
    _hw_tick()
    assert not hw.z_trigger and not hw.z_done

    hw.z_location = SAFE_Z
    hw.z_trigger = True
    _hw_tick()
    assert hw.z_done and hw.z_motor_stable, "Z raise failed"
    hw.z_trigger = False


def dispense_to_well(well_pos: int, volume_ul: float) -> None:
    """
    Full dispense sequence: move XY → lower Z → dispense → raise Z.
    """
    if not ctrl.autorun or not ctrl.estop_ok:
        return

    # Step 1 — XY positioning  [Pattern A: trigger sequence #4 — identical to aspirate Step 1]
    hw.xy_trigger = False
    hw.xy_done = False
    _hw_tick()
    assert not hw.xy_trigger and not hw.xy_done

    hw.xy_target = well_pos
    hw.xy_trigger = True
    _hw_tick()
    assert hw.xy_done, "XY positioning failed"

    # Step 2 — Z lower to well  [Pattern A: trigger sequence #5 — identical to aspirate Step 2]
    hw.z_trigger = False
    hw.z_done = False
    _hw_tick()
    assert not hw.z_trigger and not hw.z_done

    hw.z_location = _well_z(well_pos)
    hw.z_trigger = True
    _hw_tick()
    assert hw.z_done and hw.z_motor_stable, "Z lower failed"

    # Step 3 — ADP dispense
    # Note: volume must be cleared to 0 first — driver detects new command
    # by 0→nonzero transition (same as PLC F20_06 Step 110 quirk).
    # [Pattern B: reads hw.adp_draw_done to infer pump has liquid]
    hw.adp_arrange_volume = 0
    hw.adp_arrange_trigger = False
    hw.adp_arrange_done = False
    _hw_tick()
    assert (not hw.adp_arrange_trigger
            and not hw.adp_arrange_done
            and hw.adp_arrange_volume == 0)

    hw.adp_arrange_volume = int(volume_ul * 10)
    hw.adp_arrange_trigger = True
    _hw_tick()
    assert hw.adp_arrange_done, "ADP dispense failed"
    hw.adp_arrange_trigger = False

    # Step 4 — Z raise to safe height  [Pattern A: trigger sequence #6 — identical to aspirate Step 4]
    hw.z_trigger = False
    hw.z_done = False
    _hw_tick()
    assert not hw.z_trigger and not hw.z_done

    hw.z_location = SAFE_Z
    hw.z_trigger = True
    _hw_tick()
    assert hw.z_done and hw.z_motor_stable, "Z raise failed"
    hw.z_trigger = False


def wash_tips(wash_pos: int, wash_volume_ul: float, cycles: int = 3) -> None:
    """
    Wash cycle: alternating aspirate + dispense at wash station.
    [Pattern C: Layer-3 orchestration calling Layer-2 operations]
    """
    if not ctrl.autorun or not ctrl.estop_ok:
        return
    for _ in range(cycles):
        aspirate_from_well(wash_pos, wash_volume_ul)
        dispense_to_well(wash_pos, wash_volume_ul)


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    aspirate_from_well(well_pos=3, volume_ul=20.0)
    print("aspirate OK, Z =", hw.z_actual)

    dispense_to_well(well_pos=10, volume_ul=20.0)
    print("dispense OK, Z =", hw.z_actual)

    wash_tips(wash_pos=0, wash_volume_ul=50.0, cycles=2)
    print("wash OK")

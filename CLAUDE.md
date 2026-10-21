# Carvera Controller — working notes

Context for Claude sessions in this checkout. Sessions append here sequentially;
**add a new section rather than rewriting an existing one**, and if you
contradict something below, say so explicitly rather than silently editing — a
later session can't tell a correction from a clobber.

Companion notes, with pointers both ways:

| File | Contents |
|---|---|
| `~/Desktop/shenanigans/z1_files/CLAUDE.md` | The machine itself: G-code gotchas, 4th axis, feeds, firmware facts |
| `~/Desktop/shenanigans/z1_files/Z1_REFERENCE.md` | Firmware internals: pin maps, container format, full command list |
| `~/Desktop/shenanigans/z1_files/macropad_pendant/README.md` | Pendant user docs: key layout, chords, settings |
| `~/Desktop/shenanigans/z1_files/macropad_pendant/PROTOCOL.md` | Pendant serial protocol, the contract between firmware and driver |

Z1 firmware source is at `~/Desktop/shenanigans/z1_files/MakeraZ1Firmware/`.
Most claims below were read out of that source rather than inferred — when a
question is "what does the machine actually do", read it there.

---

## This checkout

- Remote `origin` = `Carvera-Community/CarveraController`, `fork` =
  `guutz/Carvera_Controller` (the user's). Work branch **`z1-usb-macropad`**,
  pushed to `fork`. ~32 commits ahead of `origin/develop`; develop is an
  ancestor, so it is a clean fast-forward base.
- The **Mac app launches from this working tree** —
  `~/Applications/Carvera Controller.app` runs `poetry run python -m
  carveracontroller` here. So a restart picks up uncommitted edits. Nothing is
  hot-reloaded: Kivy reads `.py` and `.kv` once at startup.
- That `.app` is an `osacompile` applet, not a shell script — a bash script as
  `CFBundleExecutable` has no Mach-O header and macOS calls it Intel-only. Its
  `launch.sh` must export `PATH=/opt/homebrew/bin:...` because GUI apps don't
  inherit the shell PATH and poetry would otherwise pick Xcode's python3.9 and
  build an empty venv. Log: `~/Library/Logs/CarveraController-launcher.log`.

### Running tests

Use the **poetry venv**, which has every dependency:

```bash
P=~/Library/Caches/pypoetry/virtualenvs/carvera-controller-community-RzN3lVxT-py3.12/bin/python
KIVY_NO_ARGS=1 KIVY_LOG_MODE=PYTHON $P -m pytest tests/unit -q \
  --ignore=tests/unit/test_pendant_display.py --ignore=tests/unit/test_whb04_input.py
```

Those two files need the **`hidapi` native library**, which isn't installed on
this Mac. Pre-existing and unrelated to any change — they fail at *collection*,
which aborts the whole run if not excluded. Full suite is ~1163 tests, ~10s.

Traps worth knowing:

- `pytest -k "macropad or whatever"` still **collects the entire suite**, so it
  hits those same import errors. Filter by explicit file list, not `-k`.
- A scratchpad venv used earlier lacked `quicklz`, so anything importing
  `carveracontroller.main` failed there. Several tests do import it
  (`test_halt_estop_note`, `test_apply_bed_settings`, `test_settings_persistence`)
  — that's an accepted pattern, not a constraint. Use the poetry venv.
- `ruff check` and `ruff format --check` over `carveracontroller/` and
  `tests/unit/` before committing. Ruff is not on PATH; it's in the venvs.

---

## The theme: capability detection

Most Z1 bugs in this app are one shape — **a flag that doesn't mean what its
name suggests**. Check the meaning before gating on it.

### `is_community_firmware` — a Z1 never satisfies it

`main.py` sets it as `"c" in fw_version.lower()`. The Z1 reports
`version = 1.1.2.0.1.13`, so it is **always False**, and the Community firmware
doesn't support the Z1 at all (its `Kernel.h` has `CARVERA_AIR` but no
`MAKERA_Z1`).

It gates ~68 sites in `makera.kv`, but lopsidedly: **60 of them are the WCS
offset table** (G54–G59.3 editable fields and Clear buttons). The 8 real feature
gates are Set Rotation, Auto Ext. Out ×2, Adv Calibrate, Change to 3D probe, Set
Origin, Ext. Control, Jog mode. Plus in Python: CMM Workbench, Facing wizard,
Adv Calibrate popups, the non-community nag, the **USB baud upgrade**, and WCS
rotation.

**Do not widen it to include the Z1.** It would switch on WCS editing, the
facing wizard and a baud upgrade against firmware implementing none of them. Add
a narrow capability flag instead — the codebase already does this
(`supports_camera`, `supports_auto_ext_out`, and now `supports_3d_probe`).

### `app.has_4axis` means *the loaded file* is 4-axis

Set by the **G-code parser** when it meets an `A` word (`CNC.py`), reset in
`CNC.init()`. So it is False with no file open, and False for a 3-axis job on a
machine that has a rotary. It is **not** hardware presence. Gating the headstock
probe on it was a real bug.

Nothing reports the hardware. The firmware knows — `Endstops.cpp` sets
`axis_is_on[A_AXIS]` during homing — but never puts it in the status string, and
`MPos` carries A and B regardless of what's fitted. So the source of truth is the
user's declaration: the pendant setting `macropad_show_a_axis`, retitled
"MacroPad: Machine Has A 4th Axis" because it now gates actions too.

### `CNC.vars` — live, and dead

Parsed from **every status poll** (`Controller.py`):

`active_coord_system` (the `G:` field) · `wcox/wcoy/wcoz/wcoa` (computed
rotation-aware from `mx − wx`) · `mx/my/mz/ma`, `wx/wy/wz/wa` · `rotation_angle`
· `inch_mode`, `absolute_mode` (the `C:` field) · `curfeed/tarfeed`,
`curspindle/tarspindle`, overrides · `tool`, `tlo`, `target_tool` ·
`MachineModel`, `FuncSetting`, `vacuummode`, `spindletemp`, `extoutmode`

**Initialised and never written** — bCNC leftovers that look authoritative:

`WCS` (reads `"G54"` forever — use `active_coord_system` and `CNC.wcs_names`) ·
`units` · `distance` · `motion` · `plane` · `arc`

`absolute_mode` is parsed on every poll and **read by nothing**. `inch_mode` is
read only to set `UnitScale`. Polarity from `Robot.cpp`: `G90 → absolute_mode 1`,
`G20 → inch_mode 1`; `inch_mode == 999` is the status parser's "unknown"
sentinel.

---

## 3D probing on the Z1

### Tool numbers

| | 3D probe tool |
|---|---|
| Community firmware / this app's default | `PROBE_3D_TOOL_NUMBER = 999990` (range 999990–999999) |
| **Z1/Z1Pro stock firmware** | **`T9999`** (`Z1_PROBE_3D_TOOL_NUMBER`) |

`999990` appears nowhere in Z1 firmware. `CNC.probe_3d_tool` holds the
model-appropriate number, set from `probe_3d_tool_for_model()` when the machine
reports its model. Two predicates, deliberately distinct:

- `is_3d_probe_tool(n)` — *the* 3D probe (display). Narrow.
- `is_probing_tool(n)` — anything the probing screen can drive (gating). Covers
  the whole range plus the Z1 sentinel.

Collapsing them regresses custom probes T999991–999999 from "PROBE" to "3DPRB";
the pendant tests catch it.

**Why it matters beyond naming:** selecting the stock "Probe" entry (T0) on a Z1
runs `fill_cali_scripts(is_probe=true)`, which measures tool length and then
issues `M492.3` — the Carvera *wireless*-probe liveness check — and halts with
"Probe dead or not set, please charge or set first!". T9999 takes the same path
minus that check, and drives the wired probe laser. This was the original
reported symptom.

### The M480 family

Z1 stock firmware implements **none of M460–M469** (the Community probing set).
It has its own:

| Command | Operation |
|---|---|
| `M480.1`–`.4` `D X Y Z` | Outside corner, four quadrants |
| `M480.5`–`.8` | Inside corner, same quadrants |
| `M480.9` | Inside pocket / bore |
| `M480.10` | Outside pocket / boss |
| `M495.3 D H` | XYZ corner probe (already wired as `Controller.xyzProbe`) |
| `M495 X Y O` (no `F`) | **4th-axis headstock Z probe** → `G10 L20 P0 Z<rotation_offset_z>` |
| `M495 X Y O F` | Z probe at XY offset |
| `M495 X Y C D` | Margin |
| `M495 X Y A B I J H` | Auto-levelling |
| `G38.2`–`G38.5` | Straight probe, any axis |

Mapping lives in `addons/probing/operations/Z1Probing.py`, with the derivation in
its module docstring. **The dangerous part: the two firmwares disagree on Y.**
Community `M464` moves −Y and probes +Y for a positive Y distance; Z1 `M480`
moves +Y and probes −Y. X agrees. So the corner tables pair by *physical corner*,
not by copying each operation's `(x_is_negative, y_is_negative)` flags — doing
that maps Top Left onto the front-left subcode and drives the probe at the work.
Mutation-tested.

X and Y go out as **magnitudes**; `M480` derives the quadrant from the subcode
and applies signs itself, so a signed argument double-negates. The screen's `E`
("below the top surface") is M480's `Z`. `strtoul` parses the full subcode, so
`M480.10` is 10 and doesn't collide with `.1`.

Single-axis probing needs no macro — it's generated from `G38.2` primitives in
the machine's own idiom (tap, retract, re-tap at half rate, write offset,
**retract again**). That last retract is not optional: `ZProbe::read_probe` runs a
motion guard that halts on any non-probe move made while the probe reads
triggered, so a cycle ending in contact turns the next jog into a "3D probe
crash" alarm.

**Unmapped, and refused** by a guard in `ProbingPopup._generate_for_machine`
rather than emitted for the Z1 to discard: angle (`M465`), probe tip (`M460.x`),
calibration (`M469.x`), 4th-axis level (`M465.1`), single-axis bore/boss.

### Probe tip diameter

Makera publish **no tip diameter** — not on the wiki page, not in the store
listing. Z1 firmware defaults `tool_dia = 2.0` for all ten `M480` subcodes
(`M495.3` defaults 3.175 = 1/8", because that cycle is for an endmill or the
separate "3D probe rod").

But the number wanted is an **effective** diameter including pre-travel, which is
smaller than the ball. Community docs: a V6 probe with a 2 mm ball typically has
an effective diameter of **1.0–1.8 mm**. `zprobe.probe_tip_diameter` is a
*Community* config key and **does not exist in Z1 config**, so there is no
machine-side fallback — which is why `D` is required for X/Y single-axis probes
on a Z1 and the hint reads "2 = default" there.

To measure it: set `D=0`, `S=0`, probe both faces of a block of known width `W`,
then **effective diameter = |X_right − X_left| − W**. `[PRB:...]` reports
*machine* coordinates (`get_axis_position` returns `machine_position`), so the
readings are comparable regardless of WCS.

---

## The MacroPad pendant

Architecture the user specified: **firmware is completely application-agnostic**
— it exposes 12 keys, 1 encoder, 12 RGB LEDs and a text grid over USB serial, and
knows nothing about CNC. All meaning lives host-side in the driver. Contract is
`macropad_pendant/PROTOCOL.md`; change both sides together.

- `addons/pendant/macropad.py` — `Daemon`, transport and protocol
- `addons/pendant/pendant.py` — `MacroPadPendant`, all the CNC meaning
- `addons/pendant/macropad_layout.json` — the key layout, data not code
- `macropad_pendant/firmware/code.py` — CircuitPython, firmware v4
- `macropad_pendant/carveracontroller-pendant` is a **symlink** into this repo

### Chord engine

Input resolves as **stenotype strokes**, not press events: the stroke accumulates
the union of keys held and resolves on last release. Press order is irrelevant,
which is what fixed the original "unpredictable depending on press/release order"
complaint. A stroke that jogged (encoder turned) is **spent** and won't also fire
whatever chord those keys spell.

### The jog-key safety rule — this changed

Originally **"no chord uses a jog key"**, after a reported accident: holding X to
jog and brushing SET on the way out spelled `{X, SET}` = ZERO XY, which fired
twice. `_stroke_jogged` only covers the case where you actually turned the
encoder.

It is now the narrower property that was doing the real work:

> **A chord sharing a key with jogging must confirm.**

Enforced in `validate_macropad_layout` — whole layout rejected, with the fix in
the message, and a per-binding `confirm: false` cannot defeat it. This is what
let the rotary jog go on **key 5** (directly under Z) on a pad with all twelve
keys already spoken for, with no binding moved: `SET+5` Zero Z already confirmed,
and `ACT+5` Spindle now does via the layout's per-binding override.

### Default layout

```
row 0:  0=X    1=Y    2=Z          jog
row 1:  3      4      5=A          targets, and the rotary jog
row 2:  6      7      8            targets
row 3:  9=GOTO 10=ACT 11=SET       modifiers
```

GOTO: margin, level, machine home, safe Z, work home ·
ACT: run/pause, stop, spindle *(confirms)*, probe Z, macro 1, probe laser ·
SET: zero XY, probe 4th axis, zero Z

Layout validation is **all-or-nothing**: a malformed or unknown-action file is
rejected whole and the shipped default used, with the reason logged — the pendant
never comes up half-bound. A user copy in the app config dir wins over the
shipped one.

### Macros

`pendant_macro_1..10`, JSON `{"name": ..., "gcode": ...}`, sent line by line.
The pendant shows the macro's **own name** on the OLED. Useful one, now that it
exists:

```
M495 X0 Y0 O0
```

That is the entire "prompt for the probe, probe the 4th-axis headstock, set work
Z to the rotation centreline" sequence — the firmware does the tool-change prompt
itself when `active_tool != 0`. `X0 Y0` only exist to enter the parser branch;
`zprobe_abs` ignores them. **Don't confirm that tool-change prompt with the 3D
probe still fitted** — it changes to T0 and runs the wireless liveness check.

### Firmware v4 notes

RP2040 has **no DAC**; audio is PWM via `audiopwmio`, but `synthio` still gives
12-voice polyphony, so chords sound as chords. Keys are mapped up a **major
pentatonic** scale (no semitone, no tritone, so any key pair is consonant however
the layout is remapped), **inverted** so the bottom row — the modifiers, present
in every chord — is the bass. Sine wavetable at 30% amplitude, because
full-scale voices clip the moment two sound together. All three of those were
hardware-reported problems ("shrill and distorted").

---

## Running a job: buffer, pre-run, resume

### `buffer`

A firmware command, not G-code. `Player::buffer_command` pushes the line onto a
`buffered_queue`, drained one line per main-loop pass and **only when not halted,
suspending, waiting or inner_playing**. So buffered lines run *when the machine
leaves the paused state*. That is why the resume preamble stages everything with
`buffer` and then sends `resume`.

### Pre-run check

`play()` now defers to `open_pre_run_check_popup()` **before** `apply()`, because
`apply()` already buffers the margin/probe/level sequence to the machine — a
dialog after it would be asking about commands already handed over. Resume keeps
its own confirmation and is not double-prompted. Off switch: Settings → *Confirm
Coordinate System Before Running* (`show_pre_run_check`).

It names the active WCS and its offsets, which WCS the file selects, and warns on
`wcs_mismatch`, `wcs_multiple`, and `zprobe_overwrites_z`. **Margin and
auto-levelling are deliberately not flagged**: verified that neither
`fill_margin_scripts` nor `fill_autolevel_scripts` contains a `G10`, while
`fill_zprobe_scripts` ends in `G10 L20 P0 Z<probe_height_mm>`. Warning about
steps that change nothing trains people to dismiss the dialog.

### Resume-at-line

The preamble **reconstructs modal state by scanning the file backwards** for the
last `G20/G21`, `G90/G91`, WCS, `M7/M9`, `M3 S`, feed, `M6`. That is correct and
should not be replaced with live state — its job is to recreate what the *file*
had established by that line, and a probe cycle or MDI command in between can
leave the machine elsewhere. (An earlier session considered swapping it; that
would have restored `G91` if the machine happened to be relative, turning the
file's absolute coordinates into offsets.)

Live state is used only to *check* the reconstruction. `resume_playback_warnings`
returns `relative_mode` (nothing restores G90/G91 and the machine is in G91) and
`unit_mismatch` (nothing restores G20/G21 and the machine's units aren't the
file's), alongside the existing tool/feed/spindle warnings.

**Open, and a live hazard — see Open questions.**

---

## Camera

Served by the ESP32 WiFi module, not the motion firmware. WebSocket on **port
82**, path `/ws_video`, send `start_stream`, then one whole JPEG per binary
message. Resolution is the only setting exposed, on port 80.

Complete route list, dumped from the ESP32 binary:

```
/ws_video  /ws_preview  /api/camera/resolution  /api/firmware/info  /update  /updateffs
```

**No HTML anywhere** — there is no web page to open in a browser, and a WebSocket
can't be viewed by navigating to it. A bundled local HTML viewer painting frames
to a canvas would work; not built.

The whole camera UI (collapsible `camera_splitter`, resolution picker, host-side
brightness/contrast/gamma) **already existed** upstream, gated on
`app.supports_camera` — zero height, zero opacity, disabled when false, so it
looks absent rather than disabled. The probe used to be gated on
`connection_type == CONN_WIFI`, for a real reason: the camera needs an IP and
`connection_address` over USB is a serial port.

Now resolved via `MachineDetector.discover_machines()` — machines announce
`name,ip,port,busy` on **UDP 3333** regardless of how the controller is attached.
The firmware's own `net` command would have been the direct route but **its body
is entirely commented out**. If several machines answer, **none is chosen**:
nothing ties a broadcast to the machine on the far end of the serial port. Needs
the Z1 to also be on WiFi; USB-only means no camera path at all.

---

## Conventions that earned their place

- **Comments say why, not what.** Several above exist because the obvious-looking
  thing is wrong (`CNC.vars["WCS"]`, `app.has_4axis`, replacing the resume scan).
- **Tests assert the property, not the mechanism.** The hint-pointer test asserts
  *brightness contrast*, not an animation name — an animation name passing
  wouldn't have caught the bug it was written for.
- **Mutation-test anything where a sign or a polarity decides whether metal gets
  hit.** Flip it, confirm the tests fail, restore. Print the *full* failure list —
  `tail -3` truncated one and made a real mutation look like a no-op.
- A **skipped** test is false coverage. One here referenced a `FourthAxisZ` enum
  member that doesn't exist (it's `FourthZ`) and silently skipped.
- When changing generated output, check tests asserting by **position**
  (`lines[-1]`) — appending a retract broke two that way.

---

## Open questions

- **Resume-at-line off-by-one — a live hazard, unresolved.** `Controller.py`
  subtracts 1 from the start line for all non-community firmware
  (`";using number-1 as goto is bugged and off by one in this fw version"`), so it
  *always* fires on a Z1. But `Player::goto_command` computes
  `completed_lines = goto_line - 1` and waits for `played_lines >=
  completed_lines`, meaning `goto N` resumes **at** line N — no off-by-one. If
  so, CCC re-runs line N−1, which matches the user's report of replayed moves
  (and a crash into the bed). Caveat: that line carries a `//zqq modify
  2026.08.04` marker, and the source tree hasn't been matched to the installed
  binary. **Not fixed, deliberately** — wrong in the current direction repeats a
  line, wrong the other way silently *skips* one, which is worse. To settle it:
  the resume dialog's `preview=True` path lists every command without executing;
  resume at a known line and compare the line the machine reports
  (`Goto line %lu...`) against the one asked for.
- Resume's `G1 Z{z}` preamble move uses a **work** coordinate reconstructed from
  the G-code viewer. Nothing checks the WCS still means what it meant at load, so
  a re-zero between load and resume makes that a feed-rate plunge to the wrong
  depth.
- The "paused workspace" idea — a feed hold that presents a known-safe set of
  operations and a defined path back — discussed, not designed.
- `/ws_preview` and `/sd/videos` exist in the ESP32 firmware and nothing in this
  app touches either. The second suggests the machine can record to SD.
- Button text overflows its buttons app-wide, worse with Windows UI scaling.
  Diagnosed only: 72 of 205 buttons set `text_size: self.size`, 4 use `shorten`.
  A global `<Button>` rule is complicated by ~15 widgets that size from
  `texture_size`.
- Not yet offered in the layout editor: adding or removing **modifiers** (JSON
  only).

---

## Upstream merge, 2026-10-20

*Added 2026-10-20 by the same controller session, after merging 208 commits of
`origin/develop` (the 2.3.0-RC1 work) on branch `merge/upstream-develop-2026-10`.
Two corrections to the sections above are marked **Correction**.*

### Correction: the `is_community_firmware` widget list was mislabeled

"The 8 real feature gates" above names **Set Origin** and **Ext. Control**. Both
are wrong: the label-finder walked *backwards* from each `disabled:` line and
picked up the previous widget's text. Resolved by indentation to the enclosing
widget, the control-screen gates are **Probing…** and **Tools…** (a `Spinner`).
Set Origin is only state-gated; Ext. Control isn't gated. The rest of the list
(Set Rotation, Adv Calibrate, jog mode, Auto Ext. Out) was right. If you need
this list again, resolve each `disabled:` to its enclosing widget by indentation
— reading backwards *or* forwards from the line both overshoot.

### Correction: test invocation

Add `--ignore=tests/unit/test_continuous_jog_state.py` — upstream's new test
imports `WHB04`, which only exists when `hidapi` loads. And expect **five
failures that predate this branch**, reproduced identically on a clean
`origin/develop` worktree:

```
test_gcode_viewer_stock.py  test_simulation_available_{with_cam_tool_table,laser_only_without_tool_table,laser_and_3d_probe_without_tool_table}
test_tooltips.py            test_live_text_change_does_not_add_top_gap
test_tutorial.py            test_welcome_doc_link_opens_from_its_on_screen_position
```

Not lock drift — upstream's `poetry.lock` changes were patch bumps to dev
tooling. Suite is now ~1433 passing. `ruff` is in the poetry venv's `bin/`.

### What upstream changed that matters here

- **Kivy properties must not be set from the serial thread.** `monitorSerial`
  runs off the main thread. Upstream `a91c421` moved firmware-derived property
  writes into `_apply_detected_firmware`, scheduled via `Clock.schedule_once`.
  Put anything derived from the version line there, not in `monitorSerial`.
  (The disconnect block in `monitorSerial` still sets `app.model` and friends
  directly — upstream's own remaining pattern, left as-is.)
- **Tool menus are rebuilt from scratch**, now by `_refresh_tool_dropdowns()`
  (extracted in the merge from upstream's inline code). "3D Probe" follows
  `supports_3d_probe`, "Laser" `is_community_firmware`. It runs from
  `_refresh_3d_probe_support()`, which both the firmware and model handlers call,
  because those two status lines arrive in either order.
- **Upstream gated the main-menu probe button on `is_community_firmware`**
  (`3d9c81f`), which would have disabled Z1 probing outright. Both probing entry
  points — that icon and the control screen's Probing… — now use
  `supports_3d_probe`. Before the merge the control-screen one was still
  community-gated on this branch too; probing was reachable on a Z1 only via the
  main menu and the pendant.
- **Continuous jog: use `Controller.continuousJogBusy()`, not
  `continuous_jog_active`.** Upstream `b1d1507` dropped the firmware-message
  handler that cleared stuck jog state, so the timeout inside
  `continuousJogBusy()` is the *only* recovery from a lost `^Y` ack. Any pendant
  checking the bare flag gets stuck after one. The MacroPad is moved over, with a
  regression test.
- **`play()` now lifts to clearance first** (`liftToClearance(buffer=True)`,
  `7aada69`) and is blocked during the new guided tour (`tutorial_blocks_motion()`,
  a pure flag). Both live in `_start_play` behind the pre-run check, since
  anything buffered must come after the dialog.
- **Upstream did not add Z1 probing.** No `T9999`, no `M480`. Our probing work
  remains the only Z1 path.
- Camera: upstream added reconnect-with-spinner (`72b0ca1`) and **Z1 timelapse**
  (`c0a089f`, `timelapse.py`, recording state on the machine link — so the
  `/sd/videos` route noted above is now used). Neither derives the camera host
  on its own, so USB discovery composes with both.
- Also arrived and relevant to this machine: **firmware updater support for Z1**
  bundled lpc+esp (`beefe2b`), USB bulk link probe for connect/disconnect
  tracking (`21ca19f`).

### Merge state, as of writing

`merge/upstream-develop-2026-10` holds the merge commit, **local only, not
pushed**. `z1-usb-macropad` is untouched until the user confirms — then it is a
fast-forward to that branch.

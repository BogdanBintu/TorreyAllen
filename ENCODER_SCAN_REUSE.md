# Reusing an ASI Tiger Z encoder scan from Dave

Based on TorreyAllen master commit c7fb13191fa9860905fd216ab08ca2f0141a6a0d.

Add `<reuse_encoder_scan>True</reuse_encoder_scan>` inside a Dave `<movie>`
to reuse the last prepared absolute Z scan range. Omit it, or set it to `False`,
to query the current motor Z position and prepare a fresh range for that movie.
The option belongs to each movie request, not the saved HAL parameter set.
The Record button and old Dave recipes retain the existing full preparation.

## Install

Close HAL and Dave. The download contains the eight modified Python files in
their repository-relative folders, two test files, an example recipe, this
guide, and `encoder_scan_reuse.patch`.

For an unmodified checkout of the base commit, copy the included
`storm_control` folder into `C:\TorreyAllen\storm_control`, merging folders and
replacing the eight matching Python files. Use the same patched checkout for
both HAL and Dave, then restart both programs.

If your checkout contains other changes, use the patch instead of replacing
whole files. From the repository root, with the patch saved there:

```powershell
cd C:\TorreyAllen
git apply --check encoder_scan_reuse.patch
git apply encoder_scan_reuse.patch
```

Only run the second command when the check succeeds. The patch does not change
the HAL startup XML, the four-camera settings, camera trigger mode, or XY motion.

Check your Dave shortcut: this repository's `bats/Dave-Automation.bat` currently
launches a different checkout under `C:\Software\Mammoth`. Update that shortcut
to run `C:\TorreyAllen\storm_control\dave\dave.py` with the desired Dave settings,
or install the modified Dave files into the checkout you actually launch.

## Dave recipe

Load `Paramaters/Hal_paramaters4cam.xml` when launching HAL, then load
`Paramaters/4cam_slaves_main.xml` as the active parameter set. The supplied
`Paramaters/dave_encoder_scan_reuse_example.xml` demonstrates three XY fields:
the first prepares the scan and the next two reuse its Z range.
Replace its example coordinates and filenames before running it.

```xml
<movie>
  <name>fov_001</name>
  <stage_x>1100.0</stage_x>
  <stage_y>2000.0</stage_y>
  <length>40</length>
  <reuse_encoder_scan>True</reuse_encoder_scan>
</movie>
```

Dave generates the move action before the acquisition action and waits for
the move action's completion before taking the movie. Omit `stage_z` for an
XY-only move. In this checkout, `stage_z` in a Dave move controls the Z piezo;
the encoder scan operates the separate motorized Z axis.

For a generated primitive sequence, put the same field inside `DATakeMovie`:

```xml
<DATakeMovie>
  <name type="str">fov_001</name>
  <length type="int">40</length>
  <reuse_encoder_scan type="bool">True</reuse_encoder_scan>
</DATakeMovie>
```

When generating recipes in Python, add `"reuse_encoder_scan": True` to the
movie dictionary. Direct TCP `Take Movie` requests accept the same Boolean key.
Recipe XML accepts `True` or `False`, ignoring surrounding whitespace and case.

## What runs

| Movie request | Commands at scan start |
| --- | --- |
| False or omitted | `2SCAN X?`, `W Z`, `2SCANR X=... Y=... Z=...`, `2SCAN` |
| True, with matching prepared settings | `2SCAN X?`, `2SCAN` |
| True, with no prepared range or changed settings | Full preparation automatically |

The first scan after HAL startup always prepares, even if reuse was requested.
Changing the step size, distance, nominal fps, start offset, or enabled state
invalidates the cached setup. Reapplying identical parameters preserves it.
A failed preparation invalidates it too. The active/idle checks and marking
`scan_started` before issuing the start command are retained. The idle parser
also accepts both `:A I` and `:A X=I` replies.

The existing startup order is retained: illumination preparation completes,
all slave cameras arm, HAL synchronizes, then the Tiger module starts motion
during the master startup phase. Stop handling still stops the scan before
the slave cameras. The per-movie flag resets after the film ends.

## Choosing when to reuse

Reuse keeps exactly the previous **absolute motor Z start and end positions**.
It does not calculate a new range relative to the current Z. Nearby XY fields
can share a range when that range covers the desired sample depth at all of
them. Request `False` when a new range is needed, after changing the motor Z
coordinate origin, after a controller reset or external SCAN/SCANR changes,
or when starting a new group of fields at a different depth.

For the supplied settings, a fresh preparation uses the current hardware Z
plus 3 micrometers as the start and another 12 micrometers as the end. It sets
an encoder divider of 30 and a nominal speed of 12 micrometers/second. The
current implementation's sign convention is the hardware motor coordinate;
the GUI Z stage uses an inverted sign.

This option removes two serial command round trips per reused acquisition.
It does not remove physical Z prepositioning, acceleration/return motion,
camera arming, or output-file startup. Another existing delay is the fixed
2.5-second allowance in `TigerStageFunctionality.calculateMoveTime()`, which
Dave waits out after each XY move. That can dominate the delay for nearby
fields. These timing and motion behaviors require measurement on the scope.

The PVCAM slave acquisition branch still uses `TRIGGER_FIRST_MODE`. Check
that setting separately if each frame must be independently triggered by a
Z encoder pulse; this patch does not change camera acquisition behavior.

## Validation

Run the focused tests from the repository root in the acquisition environment:

```powershell
python -m unittest storm_control.test.test_tiger_encoder_scan storm_control.test.test_dave_encoder_scan -v
```

They exercise fresh/reused command sequences, settings invalidation, failed
preparation, active-scan protection, explicit stop handling, XML generation,
TCP serialization, per-movie request propagation, and startup phase selection
using a simulated Tiger controller. They do not establish timing or behavior
on physical ASI/camera hardware. Before a long run, acquire two adjacent fields
and check the scan range and frame count for every camera.

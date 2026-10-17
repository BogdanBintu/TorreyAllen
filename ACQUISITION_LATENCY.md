# Diagnosing the time between Dave movies

This is an incremental update for the previous encoder scan reuse patch.
The update adds optional acquisition timing and removes one duplicate
Lumencor WAKEUP request per power update. It keeps the existing scan reuse
option and camera/scan synchronization.

## First comparison: turn Live Mode off

Uncheck **Live Mode** in HAL before running the five-movie test. The UI enables
it by default. With Live Mode checked, `Film.stopFilmingLevel2()` starts all
four cameras again after a movie; the next movie immediately stops them and
then starts them again for acquisition. This extra SDK start/stop cycle is
unnecessary when you only need images during Dave movies.

The fixed 2.5-second XY move allowance does not run when no DAMoveStage action
is present. Do not attribute this stationary test's delay to that allowance.

## Install this update

Close HAL and Dave. This ZIP contains four replacement Python files, the new
`storm_control/sc_library/acquisitionTiming.py` helper, a test file, this guide,
and an incremental `acquisition_latency.patch`.

Apply it to the same checkout in which you installed the scan reuse patch.
Either merge the included `storm_control` folder into the matching repository
folder, or apply the incremental patch from the repository root:

```powershell
git apply --check acquisition_latency.patch
git apply acquisition_latency.patch
```

Run the second command only if the check succeeds. If your source has other
edits, use the patch to preserve them. This patch is based on the prior
downloaded version, not unmodified GitHub master.

## Record a timing log

In the activated acquisition Python environment, set the variable before
launching HAL. From Anaconda Prompt / Command Prompt:

```bat
set HAL_ACQUISITION_TIMING=1
python C:\TorreyAllen\storm_control\hal4000\hal4000.py C:\TorreyAllen\Paramaters\Hal_paramaters4cam.xml > C:\Data\hal_timing.txt 2>&1
```

Or in PowerShell:

```powershell
$env:HAL_ACQUISITION_TIMING = "1"
python C:\TorreyAllen\storm_control\hal4000\hal4000.py C:\TorreyAllen\Paramaters\Hal_paramaters4cam.xml 2>&1 | Tee-Object C:\Data\hal_timing.txt
```

Adjust the paths if necessary. Load `4cam_slaves_main.xml`, turn off Live Mode,
and run the same five movies with the first reuse flag False and the next
four True. Preserve the complete log. The `[ACQ ...]` timestamps all use the
same clock within HAL and are comparable across worker threads.

To turn timing off, restart HAL without the variable or set it to 0.

## How to read the log

| Event | What it measures |
| --- | --- |
| last time-base frame -> next first time-base frame | The gap visible in frame delivery; files can still be flushing after the last frame |
| time-base camera finished | The timing camera's thread has finished, including its SDK stop |
| camera SDK start / camera SDK stop | Each camera's actual SDK call duration |
| camera thread join | How long HAL waits for the camera thread to stop |
| close writer | File closing, including queued Zarr compression/writes and metadata |
| create writer | New output file/array setup for each camera |
| prepare film modules -> module ready | Preparation of illumination, timing, DAQ, and other registered modules |
| Lumencor HTTP | Each wake, intensity, or channel enable/disable HTTP round trip |
| Tiger serial | Every Tiger command, including start/stop and background position queries |
| film lockout locked=False | HAL has completed film teardown and can acknowledge the movie to Dave |
| start cameras phase='stop' | A between-movie live camera restart; absent when Live Mode is off (phase='run' is the acquisition startup) |

Workers can overlap, so do not add all their elapsed_ms values together.
Use chronological timestamps to identify the operations on the critical path.
Logging itself adds some console I/O overhead; disable it for final throughput
measurements after identifying the dominant operations.

## Costs present in the source

1. The reuse option skips W Z and 2SCANR. It does not skip HAL's film teardown,
   Z return/prepositioning, camera arm/stop, illumination preparation, or files.
2. The shared serial transport always sleeps `10 * wait_time` before reading.
   Tiger currently uses the default `wait_time=0.01`, so every serial command
   incurs at least 100 ms, plus response draining and controller time. A warm
   reused movie still uses seven or more commands across start/stop, stage
   motor/joystick handling, and possibly extra stop/status polling. Some can
   overlap other preparation; queued position queries can add contention.
3. In Run Shutters Off mode, illumination restores the previous channel states
   after a movie and turns all channels on again before the next. Previously,
   each power update sent WAKEUP, SET CHINT, WAKEUP, SET CH. If all five channels
   change off/on, that is 20 requests at startup and 20 on restoration. Request
   workers can overlap, so the time is device/network dependent.
4. This update removes the second WAKEUP inside `setPower()`. The sequence is
   now WAKEUP, SET CHINT, SET CH. Direct `setLaserOnOff(True)` retains its wake.
   This reduces the request count for those ten power updates from 40 to 30,
   without caching power levels or changing channel enable/disable commands.
5. Zarr closing waits for queued frames, and HAL creates new writers for every
   movie. Camera SDK arm/stop calls can also be significant; only the timing
   log can establish their contribution on the acquisition PC.

If illumination dominates, the next larger optimization is to retain intensity
settings and gate channels with the DAQ TTL lines between movies. That needs an
explicit implementation that preserves the prior on/off states and slider
updates. If serial waits dominate, use complete-response reads with timeouts
rather than blindly lowering the fixed sleep. If camera startup dominates,
keeping cameras armed across movies requires a different acquisition lifecycle.

## Verification

The focused test command is:

```powershell
python -m unittest storm_control.test.test_tiger_encoder_scan storm_control.test.test_dave_encoder_scan storm_control.test.test_acquisition_latency -v
```

The 24 tests cover scan reuse behavior and request propagation, the reduced
Lumencor request count and retained command order, and timing error propagation.
Hardware and network timing have not been measured in this environment.

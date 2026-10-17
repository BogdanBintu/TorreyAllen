# Measured movie gaps and optional faster Tiger communication

Install after the encoder scan reuse and acquisition latency updates.
This incremental update changes only `tiger.py` and the Tiger constructor call
in `tigerModule.py`, plus a test file and this guide. It requires the
`acquisitionTiming.py` helper from the preceding update.

## Results from the supplied hal_timing.txt

The five movies were recorded with Live Mode off during acquisition. The
start of HAL initially had Live Mode on, but the five `phase='run'` startup
events all report `live=False`, and there are no live restarts between movies.
The four reused movies report `reuse=True` and reuse the previous SCANR range.

| Transition | Last frame timestamp | Next first frame timestamp | Gap (seconds) | First-to-first interval (seconds) |
| --- | --- | --- | --- | --- |
| bleach_0_0 -> bleach_0_1 | 35832.268561 | 35833.226977 | 0.958416 | 2.329037 |
| bleach_0_1 -> bleach_0_2 | 35834.577857 | 35835.526720 | 0.948863 | 2.299743 |
| bleach_0_2 -> bleach_0_3 | 35836.875998 | 35838.085533 | 1.209535 | 2.558813 |
| bleach_0_3 -> bleach_0_4 | 35839.449013 | 35840.613050 | 1.164037 | 2.527517 |

These are times of frame delivery to HAL, not exposure measurements. The
first-to-last delivered frame interval for each 40-frame movie is 1.35-1.37 s.
This log does not show the previously reported four-second dead time.

Tiger commands consistently take approximately 111 ms because the shared
legacy RS232 transport sleeps for 100 ms before reading and then waits again
while draining the response. In a representative gap, the consecutive commands
J X+ Y+, MC X+ Y+ Z+, J X- Y-, MC X- Y- Z+ occupy 445 ms. They run even when
Dave does not move XY. The first pair restores motor/joystick control after
the previous movie; the second pair prepares the next encoder movie.

The two longer gaps also include 2SCAN X=80 and an additional 2SCAN X? query,
adding approximately 223 ms of command time. Keep those stop/idle checks.
The apparent wait for the timing module overlaps Tiger preparation; it is
not evidence that the timing module itself sleeps for that interval.

Other measured costs:

| Operation | Observed cost |
| --- | --- |
| Four Zarr writer closes combined | Approximately 8 ms per movie |
| Four new writers combined after the first movie | Approximately 31-34 ms |
| Camera SDK starts | Approximately 6-14 ms per camera, mostly parallel |
| Camera SDK stops | Less than 1 ms per camera |
| Next-film illumination preparation to ready | Approximately 66-91 ms, overlaps Tiger preparation |
| Sending 2SCAN to first delivered frame | Approximately 0.30 s; not all of this is the serial acknowledgment |

The first writer of the very first movie takes about 397 ms to create,
consistent with a one-time initialization cost. Later first-camera writers
take about 9 ms, so this is not the steady-state bottleneck.

## Install

Close HAL. Prefer applying the included `tiger_fast_serial.patch` from the
repository root, because it preserves other edits in your files:

```powershell
git apply --check tiger_fast_serial.patch
git apply tiger_fast_serial.patch
```

Run the second command only when the check succeeds. The ZIP also contains
replacement versions of the two Python files if you have the preceding
downloaded versions without other edits. The patch does not replace your
`tigerEncoderScan.py`.

## Enable

Inside the existing `<tiger_controller><configuration>` section of
`Paramaters/Hal_paramaters4cam.xml`, alongside `port` and `baudrate`, add:

```xml
<fast_serial type="boolean">True</fast_serial>
<serial_response_timeout type="float">1.0</serial_response_timeout>
```

Restart HAL, keep timing enabled, and repeat the five-movie test with Live Mode
off. No Dave XML changes are needed. Setting fast_serial to False, or omitting
it, retains the legacy reader. The timeout is an upper bound, not a fixed delay.

## How the faster reader works

ASI documents CR/LF reply termination and an optional CR-only mode. For known
single-line command families, the faster reader waits until the CR arrives
instead of sleeping for 100 ms. Packet fragments and short pySerial read
timeouts are accumulated until a complete response or the overall deadline.
Any LF left after the CR is retained as harmless leading whitespace in the
next reply. Blank acknowledgments in Tiger reply syntax remain valid.

WHO, build reports, help, and unknown command families retain the legacy
reader because their replies can contain multiple lines. The shared RS232
driver and other hardware manufacturers' drivers are not changed.

The reader never retries a command on timeout. A timed-out SCAN may already
have started motion, so resending it could toggle the motion. After a timeout,
later serial commands are rejected to prevent an old reply from being assigned
to a new command. Ensure motion has stopped using the controller, then restart
HAL before continuing. The original controller mutex remains responsible for
serializing access.

This removes known software sleeps. It does not establish the new hardware
cycle time in advance or eliminate stage return/prepositioning, exposure,
camera timing, or the time until a frame reaches HAL. Compare the new timing
log against the measured table above.

Your supplied log has no 2SCAN X? query immediately before scan starts, whereas
the earlier downloaded start_scan implementation included that idle check.
That suggests an additional local change. Keep the active/idle guards when
editing the scan helper; this transport update does not require removing them.

## Verification

```powershell
python -m unittest storm_control.test.test_tiger_encoder_scan storm_control.test.test_dave_encoder_scan storm_control.test.test_acquisition_latency storm_control.test.test_tiger_fast_serial -v
```

All 32 focused tests passed with simulated hardware. They cover scan reuse,
Dave request propagation, Lumencor command preservation, timing error behavior,
fragmented serial packets, CR/LF handling, legacy fallback, and timeout/no-retry
behavior. Physical hardware validation remains to be done on the microscope.

Protocol reference: https://docs.asiimaging.com/products/serial_commands

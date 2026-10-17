@echo off
set "HAL_ACQUISITION_TIMING=1"
cmd /K ""C:\ProgramData\anaconda3\Scripts\activate.bat" "merfishpp" & "python" "C:\TorreyAllen\storm_control\hal4000\hal4000.py" "C:\TorreyAllen\Paramaters\Hal_paramaters4cam.xml" > "C:\Data\hal_timing.txt" 2>&1"
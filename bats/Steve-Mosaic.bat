set "NUMBA_CACHE_DIR=%LOCALAPPDATA%\napari_numba_cache"
if not exist "%NUMBA_CACHE_DIR%" mkdir "%NUMBA_CACHE_DIR%"
set "QT_API=pyqt5"
cmd /K ""C:\ProgramData\anaconda3\Scripts\activate.bat" "merfishpp" &  "python" "C:\TorreyAllen\storm_control\steve\steve.py" "C:\TorreyAllen\Paramaters\steve_settings_default.xml""
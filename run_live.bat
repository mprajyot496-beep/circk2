@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ============================================================
echo   Live Hindi cricket commentary  (offline Piper TTS)
echo   Pick a match, then it talks until you close this window.
echo ============================================================
echo.
python main.py %*
pause

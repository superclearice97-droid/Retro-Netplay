@echo off
cd /d "%~dp0"
rem For the person SHARING this: builds a single NetplayMatchmaker.exe that needs no Python.
py -3 -m pip install pyinstaller pygame
py -3 -m PyInstaller --onefile --windowed --name NetplayMatchmaker --hidden-import server app.py
echo.
echo Done. Share dist\NetplayMatchmaker.exe
pause

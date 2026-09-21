@echo off
rem Double-click this to start MeshWX.
rem
rem A console window opens and stays open while MeshWX is running. Closing that window, or
rem pressing Control-C in it, stops MeshWX. Nothing is installed.
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo MeshWX needs Node to hand these files to your browser.
  echo Install it from https://nodejs.org ^(the green LTS button^), then run this again.
  echo.
  pause
  exit /b 1
)
node serve.mjs --open
pause

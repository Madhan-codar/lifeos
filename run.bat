@echo off
cd /d "%~dp0"

echo ═══════════════════════════════════════════
echo    LifeOS — Auto-Apply Changes
echo ═══════════════════════════════════════════
echo.

echo [1/4] Dry-run — preview changes...
python apply_changes.py --dry-run
echo.

echo Press ENTER to apply for real, or Ctrl+C to cancel.
pause >nul

echo.
echo [2/4] Applying changes...
python apply_changes.py

echo.
echo [3/4] Pushing to Google Apps Script...
call clasp.cmd push

echo.
echo [4/4] Pushing to GitHub...
git add .
git commit -m "Auto: apply changes %date% %time%"
git push

echo.
echo ═══════════════════════════════════════════
echo    DONE — Wait 20s, then reopen APK
echo ═══════════════════════════════════════════
echo.
pause
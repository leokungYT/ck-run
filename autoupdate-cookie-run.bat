@echo off
setlocal enabledelayedexpansion
pushd "%~dp0"

:: =========================================================
:: Auto Update Script for Cookie Run Bot
:: =========================================================
:: Repo: https://github.com/leokungYT/ck-run
:: Smart location:
::   - run from INSIDE cookie-run (next to main.py) -> update in place
::   - run from a PARENT folder                     -> create/update cookie-run\
:: =========================================================

echo.
echo ============================================
echo      Auto Update: Cookie Run Bot System
echo ============================================
echo.

:: ---- Detect where we are ----
set "IN_PLACE=0"
set "TARGET_FOLDER=cookie-run"
if exist "main.py" (
    if exist "config.py" (
        set "IN_PLACE=1"
        set "TARGET_FOLDER=."
    )
)
if "!IN_PLACE!"=="1" (
    echo [WHERE] Running inside project - update IN PLACE.
) else (
    echo [WHERE] Running from parent - target folder: %TARGET_FOLDER%\
)
echo.

echo ===================================================
echo  Please choose update mode:
echo ===================================================
echo  [1] Keep old data files (Update, keep backup + config)
echo  [2] Reset all folders (Update and delete old data)
echo ===================================================
choice /c 12 /n /m "Enter your choice [1 or 2]: "
set "UPDATE_MODE=%errorlevel%"

if "!UPDATE_MODE!"=="2" (
    echo [MODE] Chosen: CLEAN UPDATE - will reset folders.
) else (
    set "UPDATE_MODE=1"
    echo [MODE] Chosen: KEEP OLD FILES - will keep folders + config.
)
echo.

:: Kill ADB and Python processes to prevent file locks
echo [PRE] Stopping ADB and Bot processes...
taskkill /f /im adb.exe >nul 2>&1
taskkill /f /im python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

set "REPO_URL=https://github.com/leokungYT/ck-run/archive/refs/heads/main.zip"
set "ZIP_NAME=cookie-run_update.zip"
set "EXTRACT_DIR=update_temp"

:: 1. Create target folder if it doesn't exist
if not exist "%TARGET_FOLDER%" (
    echo [INFO] Creating directory: %TARGET_FOLDER%
    mkdir "%TARGET_FOLDER%"
)

:: 1b. Preserve user's configmain.json when keeping old data
set "CFG_BACKED=0"
if "!UPDATE_MODE!"=="1" (
    if exist "%TARGET_FOLDER%\configmain.json" (
        copy /y "%TARGET_FOLDER%\configmain.json" "configmain.json.keep" >nul 2>&1
        set "CFG_BACKED=1"
        echo [KEEP] Saved your configmain.json
    )
)

:: 2. Download the latest version (retry up to 3 times with curl, then fallback to PowerShell)
echo [1/6] Downloading latest version from GitHub...

set "DOWNLOAD_OK=0"

:: Try curl with retries
for /L %%i in (1,1,3) do (
    if !DOWNLOAD_OK! EQU 0 (
        echo [CURL] Attempt %%i/3...
        curl -k -L --retry 2 --retry-delay 3 --connect-timeout 15 "%REPO_URL%" -o "%ZIP_NAME%" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            if exist "%ZIP_NAME%" (
                set "DOWNLOAD_OK=1"
                echo [CURL] Download successful!
            )
        ) else (
            echo [CURL] Attempt %%i failed, retrying...
            timeout /t 3 /nobreak >nul
        )
    )
)

:: Fallback to PowerShell if curl failed
if !DOWNLOAD_OK! EQU 0 (
    echo [CURL] All attempts failed. Trying PowerShell fallback...
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%REPO_URL%' -OutFile '%ZIP_NAME%' -UseBasicParsing -TimeoutSec 60; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
    if !ERRORLEVEL! EQU 0 (
        if exist "%ZIP_NAME%" (
            set "DOWNLOAD_OK=1"
            echo [PS] Download successful via PowerShell!
        )
    )
)

if !DOWNLOAD_OK! EQU 0 (
    echo.
    echo [ERROR] Download failed! Please check your internet connection.
    echo [TIP] Try: ipconfig /flushdns   then run this script again.
    popd
    pause
    exit /b 1
)

:: 3. Extract files
echo [2/6] Extracting files...
if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%"
powershell -Command "Expand-Archive -Path '%ZIP_NAME%' -DestinationPath '%EXTRACT_DIR%' -Force"

:: Identify the source directory (GitHub zips name files like 'ck-run-main')
set "SOURCE_FOLDER="
for /d %%f in ("%EXTRACT_DIR%\*") do set "SOURCE_FOLDER=%%f"

if not defined SOURCE_FOLDER (
    echo.
    echo [ERROR] Extraction failed! ZIP might be corrupt.
    popd
    pause
    exit /b 1
)

:: 4. Cleanup old img folder (pull fresh templates)
echo [3/6] Cleaning old img folder (if needed)...
if exist "%TARGET_FOLDER%\img" rd /s /q "%TARGET_FOLDER%\img"

:: 5. Copy new files from extracted zip
::    (exclude autoupdate.bat so we never overwrite the running updater)
echo [4/6] Copying new files to %TARGET_FOLDER%\...
echo autoupdate.bat>"xcopy_exclude.txt"
echo ============================================
xcopy /s /e /y /exclude:xcopy_exclude.txt "%SOURCE_FOLDER%\*" "%TARGET_FOLDER%\"
echo ============================================
del /q "xcopy_exclude.txt" >nul 2>&1

:: 5b. Restore user's configmain.json when keeping old data
if "!CFG_BACKED!"=="1" (
    copy /y "configmain.json.keep" "%TARGET_FOLDER%\configmain.json" >nul 2>&1
    del /q "configmain.json.keep" >nul 2>&1
    echo [KEEP] Restored your configmain.json
)

:: 6. Cleanup temp files
echo [5/6] Cleaning up temporary files...
del /q "%ZIP_NAME%"
rd /s /q "%EXTRACT_DIR%"

:: 7. Reset / ensure working folders (no cd, works for in-place too)
echo [6/6] Resetting working folders...

if "!UPDATE_MODE!"=="2" (
    echo [INFO] Resetting folders - Clean Update...
    if exist "%TARGET_FOLDER%\backup"                rd /s /q "%TARGET_FOLDER%\backup"
    if exist "%TARGET_FOLDER%\push-file-ck\backup"   rd /s /q "%TARGET_FOLDER%\push-file-ck\backup"
    if exist "%TARGET_FOLDER%\push-file-ck\input-id" rd /s /q "%TARGET_FOLDER%\push-file-ck\input-id"
    if exist "%TARGET_FOLDER%\shared_stats.json"     del /q "%TARGET_FOLDER%\shared_stats.json"
    echo [OK] Old data deleted.
    mkdir "%TARGET_FOLDER%\backup"
    mkdir "%TARGET_FOLDER%\push-file-ck\backup"
    mkdir "%TARGET_FOLDER%\push-file-ck\input-id"
    echo [OK] Created fresh: backup, push-file-ck\backup, push-file-ck\input-id
) else (
    echo [INFO] Keeping old folders - Data Preserved...
    if not exist "%TARGET_FOLDER%\backup"                mkdir "%TARGET_FOLDER%\backup"
    if not exist "%TARGET_FOLDER%\push-file-ck\backup"   mkdir "%TARGET_FOLDER%\push-file-ck\backup"
    if not exist "%TARGET_FOLDER%\push-file-ck\input-id" mkdir "%TARGET_FOLDER%\push-file-ck\input-id"
    echo [OK] All folders ensured and kept.
)

echo.
echo ============================================
echo      Update Successful!
if "!UPDATE_MODE!"=="2" (
    echo      Folders reset: backup + push-file-ck data
) else (
    echo      Data kept: backup + configmain.json preserved
)
echo ============================================
echo.
popd
pause

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-only Python automation bot for the Android game **Cookie Run: Kingdom** (`com.devsisters.crg`), driven through **ADB** against emulator instances (primarily **MuMu Player**). It works entirely by **OpenCV template matching**: screenshot the device, find a `.bmp`/`.png` from `img/`, tap its coordinates. There is no game API — every action is "find image → tap → wait for next image".

The bot runs on a remote 128GB machine, not the dev box (don't diagnose perf from local specs).

## Running

No build/lint/test tooling exists. It's run directly with Python on Windows:

- `main.bat` → `py login-gui.py` — **login-refresh bot** GUI (the primary/most-developed tool)
- `gen.bat` → `py gui.py` — **backup/farming bot** GUI
- Headless: `python login.py` or `python main.py` (each spawns per-device workers and reads its config file directly)
- `autoupdate-cookie-run.bat` — self-updates from GitHub (`leokungYT/ck-run`)

Dependencies (no requirements.txt): `opencv-python`, `numpy`, `pure-python-adb` (ppadb), `colorama`, `customtkinter`, `easyocr`. ADB binary is bundled at `adb/adb.exe`.

## Two bots, one engine — and the config-file trap

There are **two independent bots** that share the same engine and image-clicking primitives:

| Bot | Entry | GUI | Config file | Purpose |
|-----|-------|-----|-------------|---------|
| Backup/farming | `main.py` | `gui.py` | **`configmain.json`** | Boots fresh accounts, runs play→event→box→gacha/pet, exports a zip to `backup/` only when a valuable match is found |
| Login-refresh | `login.py` | `login-gui.py` | **`config-main.json`** | Restores existing account zips from `input-id/`, logs in, runs optional steps, exports results by outcome |

⚠️ **The two config filenames differ only by a hyphen** (`configmain.json` vs `config-main.json`) and are NOT interchangeable. Confusing them is the most common mistake here.

`login.py` does `import main as M` and reuses M's engine wholesale (ADB, screencap, `ImgSearchADB`, `wait_and_click`, root toggle, `handle_repeating`, etc.). `config.py` (imported as `C`) holds shared constants (`PACKAGE`, dirs, thresholds, `ITEM_GET_MAP`/`PET_GET_MAP`, MuMu settings). When editing a login-only step, keep it inside `login.py` and call into `M`/`C` rather than modifying `main.py`.

## Engine model (main.py)

- **Screencap**: `fast_screencap` reads raw RGBA over the ADB `screencap` socket (fast path), converts to grayscale for matching. `ImgSearchADB(img, path, threshold)` returns center coords of matches; auto-falls back between `.bmp`/`.png`.
- **Tap**: `tap()` = `input swipe x y x y 100`. `wait_and_click(name, folder=, timeout=, required=)` is the workhorse; `folder=` selects an `img/` subfolder.
- **Root toggling is central**: the game must run with **root OFF** (anti-root detection) — root is enabled *only* to push/pull account files. `enable_root`/`disable_root` go through **MuMuManager.exe** when `C.USE_MUMU_ROOT=True`, else `adb root`. Shell commands are wrapped with `su_wrap` when `C.USE_SU`.
- **Logging**: `M.log(serial, msg, color)` prints `[serial] msg` and also writes to `logs/<serial>.txt` (timestamped, ANSI-stripped). GUI log lines use bracketed step tags.

## An "account" = a set of files

An account is the game's files under two dirs, zipped together:
- `shared_prefs/` → `C.SHARED_PREFS_FILES` (incl. `Cocos2dxPrefsFile.xml`, which holds `member_id` — extract via `M.extract_member_id`)
- `files/` → `C.FILES_FILES`

Zip filenames encode results: `(2-2)+headking+trader+dragon-white+[RDNXK5360].zip`, where the `+`-joined names are matched items/pets and `[XXXXX####]` is the member id. `ITEM_GET_MAP`/`PET_GET_MAP` map a template filename → canonical name; `RECORD_ALONE` marks "weak" items not worth saving unless paired with something strong (see `decide_zip_name`).

## login.py flow & concurrency

`process_account()` per zip: `restore` (root on → wipe → push files → root off) → `start_game` → wait `check-pointevent.bmp` → `event` loop → then branch by enabled steps (`box`, `find`/`find_treasure`, `check_ruby`, `maxgacha`, `maxpet`, or `link_devid`) → `export` to an outcome dir (`backup-id/`, `random-Fail/`, `login-success/`, `id-found/`, `not-found/`, `login-failed/`, or `link-devid/`).

- **Multiprocess** (1 process/device) with **atomic file claiming**: an `O_EXCL` lock in `input-id/_locks/` decides the winner, then the zip is moved to `input-id/_processing/<serial>/`. This survives crashes and lets many devices/processes share one `input-id/` without collisions. (`main.py` uses threads instead.)
- A **popup watchdog thread** runs for the whole account, clearing `fix-space`/`disk-full`/`fixsumting` popups across all steps.
- `LoginFailed` is raised (via `_raise_if_login_failed`) the moment `login-failed.bmp` appears, aborting the account into `login-failed/`.
- Step gating: `step_on("name")` reads `LOGIN["steps"]`. Step keys use underscores; config JSON may use hyphens (`link-devid` → `link_devid`). When adding a step, update `DEFAULTS`/`STEP_LABELS` in **both** `login.py` and `login-gui.py`.

## Image assets

Templates live in `img/` and subfolders (`img/item-get`, `img/pet-get`, `img/devid`, `img/find`, `img/fin-sombut`, `img/max-gacha`, `img/item-status`). Many flows also use **hardcoded coordinates** and **swipe gestures** tuned to a specific resolution. Names like `playN.bmp`, `boxN.bmp`, `devN.bmp`, `check-point*.bmp` are referenced by string in code — renaming an image means grepping for its literal name.

## web_item.py

Optional stats page (login step `web_item`). Before each export, `record()` parses the outcome filename into a "set" + member id, dedupes by member id into `web-item/stats.json`, and re-renders `web-item/index.html` (icons from `img/item-status/`).

## Conventions

- **All comments, log messages, and GUI text are in Thai.** Match the surrounding Thai comment density and tone when editing.
- OCR (`easyocr`) is lazy-loaded once (`get_ocr_reader`) only when a ruby/find step needs it.
- Text typed into the game via `input text` must avoid shell-special characters — generated emails/passwords use `[A-Za-z0-9]` only.

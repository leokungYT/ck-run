"""
login.py — Cookie Run "login-refresh" แบบ batch
════════════════════════════════════════════════════════════════════════
อ่านบัญชี .zip จากโฟลเดอร์ input-id/ ทีละไฟล์ แล้วทำตามลำดับนี้ต่อ 1 บัญชี:

  1) restore : เปิด root → force-stop → ลบข้อมูลเดิม (clean เหมือน main.py)
               → push (คืน) ไฟล์บัญชีจาก zip กลับเข้าเครื่อง → ปิด root
  2) start   : start packet เกม (root ปิดอยู่ → เกมไม่เจอ root)
  3) event   : run_event_loops (event-back / git-item / ok-gifitem)  ← login เสร็จ
  4) box     : run_boxes (รับของ box1-5)                              [ถ้า box=1]
  5) maxpet  : run_maxpet (กด pet1 → swipe 5 รอบ → สุ่มเพ็ทจนเจอ trader) [ถ้า maxpet=1]
  6) export  : เปิด root → ดึงไฟล์บัญชี (เหมือนตอน backup เจอ id) → zip เก็บตามผล
               (เจอ item→backup-id | ไม่เจอ→random-Fail | ปิด maxgacha/maxpet→login-success) → ปิด root
  7) ลบ zip ต้นทางทิ้ง แล้วไปหยิบไฟล์ถัดไปจาก input-id/ (ไม่เก็บ _done — export แยกไปแล้ว)

engine (คลิกรูป / ADB / root toggle / event / boxes / get-pet / pull) ใช้ซ้ำจาก main.py
ตัว restore (push ไฟล์กลับ) พอร์ตมาจาก push-file-ck/push-file.py

เปิด/ปิดแต่ละ step ผ่าน config-main.json (แยกจาก configmain.json ของ main.py)
รันหลายเครื่อง/หลายโปรเซสพร้อมกันได้ — แต่ละเครื่อง "claim" ไฟล์แบบ atomic
(os.rename ย้ายเข้า input-id/_processing/<serial>/) กันแย่ง/ลุมไฟล์เดียวกัน แบ่งงานชัดเจน
════════════════════════════════════════════════════════════════════════
"""
import os
import sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# กัน UnicodeEncodeError ตอน print ข้อความไทยลงคอนโซล (console เป็น cp1252/cp874)
# ต้องทำ "ก่อน" import main (main เรียก colorama.init() ห่อ stdout ทันทีตอน import)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import re
import glob
import json
import time
import shutil
import signal
import zipfile
import tempfile
import threading
import subprocess
import multiprocessing as mp

import config as C
import main as M
from ppadb.client import Client as AdbClient
from colorama import Fore, Style, init

init(autoreset=True)

NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


# ═══════════════════════════════════════════════════════════════════════
#  config-main.json  (แยกจาก configmain.json ของ main.py)
# ═══════════════════════════════════════════════════════════════════════
LOGIN_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config-main.json")

DEFAULTS = {
    "steps": {"clean": 1, "restore": 1, "event": 1, "box": 1,
              "maxgacha": 1, "maxpet": 1, "export": 1},
    "event_rounds": C.EVENT_LOOP_ROUNDS,
    "config_name": C.CUSTOM_CONFIG_NAME,
    "input_dir": "input-id",
    "output_dir": "login-success",
    "backup_id_dir": "backup-id",
    "random_fail_dir": "random-Fail",
    "login_failed_dir": "login-failed",
    "failed_dir": "input-id/_failed",
    "claim_dir": "input-id/_processing",
    "start_wait": 15,
    "shard_size": 0,   # แบ่ง output เป็นโฟลเดอร์ย่อย part-XXXX ละกี่ไฟล์ (0 = ไม่แบ่ง กองรวมใน backup-id เลย)
}
LOGIN = dict(DEFAULTS)


def load_login_config():
    """โหลด config-main.json มาทับ default (ไม่มีไฟล์ → ใช้ค่า default)"""
    global LOGIN
    cfg = dict(DEFAULTS)
    cfg["steps"] = dict(DEFAULTS["steps"])
    try:
        if os.path.exists(LOGIN_CONFIG_FILE):
            with open(LOGIN_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in (loaded.get("steps") or {}).items():
                key = k.replace("-", "_")
                if key in cfg["steps"]:
                    cfg["steps"][key] = 1 if v else 0
            for k in ("event_rounds", "config_name", "input_dir", "output_dir",
                      "backup_id_dir", "random_fail_dir", "login_failed_dir",
                      "failed_dir", "claim_dir", "start_wait", "shard_size"):
                if k in loaded:
                    cfg[k] = loaded[k]
            print(f"{Fore.GREEN}[CONFIG] โหลด {os.path.basename(LOGIN_CONFIG_FILE)} แล้ว{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[CONFIG] ไม่เจอ {os.path.basename(LOGIN_CONFIG_FILE)} → ใช้ค่า default{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.YELLOW}[CONFIG] อ่าน config-main.json ไม่ได้: {e} → ใช้ค่า default{Style.RESET_ALL}")

    cfg["event_rounds"] = int(cfg["event_rounds"])
    cfg["start_wait"] = int(cfg["start_wait"])
    cfg["shard_size"] = int(cfg["shard_size"])
    cfg["config_name"] = str(cfg["config_name"]).strip() or C.CUSTOM_CONFIG_NAME
    LOGIN = cfg

    # push ค่าเข้า config เพื่อให้ engine เดิม (run_event_loops) ใช้ทันที
    C.EVENT_LOOP_ROUNDS = cfg["event_rounds"]
    C.CUSTOM_CONFIG_NAME = cfg["config_name"]

    enabled = [k for k, v in cfg["steps"].items() if v]
    print(f"{Fore.CYAN}[CONFIG] step ที่เปิด: {enabled} | event_rounds={cfg['event_rounds']} "
          f"| config_name='{cfg['config_name']}'{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[CONFIG] input={cfg['input_dir']} → output={cfg['output_dir']}{Style.RESET_ALL}")
    return LOGIN


def step_on(name):
    return bool(LOGIN.get("steps", {}).get(name, 1))


# ═══════════════════════════════════════════════════════════════════════
#  RESTORE — push (คืน) ไฟล์บัญชีจาก zip กลับเข้าเครื่อง
#  (พอร์ตจาก push-file-ck/push-file.py — ใช้ helper ADB/root ของ main.py)
# ═══════════════════════════════════════════════════════════════════════
def adb_push(serial, local, remote):
    try:
        subprocess.run([M.adb_path, "-s", serial, "push", local, remote],
                       capture_output=True, text=True, timeout=120, **NO_WINDOW)
        return True
    except Exception:
        return False


def get_app_uid(device):
    """หา uid ของแอพ (ไว้ chown ไฟล์ที่ push กลับ ให้เกมอ่านได้)"""
    out = M._shell(device, M.su_wrap(f"stat -c %u {C.DATA_DIR}")).strip()
    digits = "".join(ch for ch in out if ch.isdigit())
    if digits:
        return digits
    out2 = M._shell(device, f"dumpsys package {C.PACKAGE}")
    m = re.search(r"userId=(\d+)", out2)
    return m.group(1) if m else None


def target_for(fname):
    """ไฟล์นี้ต้อง push ไปโฟลเดอร์ไหน + สิทธิ์อะไร (None = ไม่รู้ปลายทาง → ข้าม)"""
    if fname in C.SHARED_PREFS_FILES:
        return C.SHARED_PREFS_DIR, "660"
    if fname in C.FILES_FILES:
        return C.FILES_DIR, "600"
    return None, None


def push_into(device, serial, local, fname, dest_dir, mode, uid):
    rtmp = f"/sdcard/{fname}"
    if not adb_push(serial, local, rtmp):
        return False
    device.shell(M.su_wrap(f"mkdir -p {dest_dir}"))
    device.shell(M.su_wrap(f"chown {uid}:{uid} {dest_dir}"))
    device.shell(M.su_wrap(f"cp {rtmp} {dest_dir}/{fname}"))
    device.shell(M.su_wrap(f"chown {uid}:{uid} {dest_dir}/{fname}"))
    device.shell(M.su_wrap(f"chmod {mode} {dest_dir}/{fname}"))
    device.shell(M.su_wrap(f"restorecon {dest_dir}/{fname}"))   # best-effort fix SELinux
    device.shell(f"rm -f {rtmp}")
    out = M._shell(device, M.su_wrap(f"[ -e {dest_dir}/{fname} ] && echo OK || echo NO")).strip()
    return "OK" in out


def restore_account(device, serial, zpath):
    """ลบข้อมูลเดิมก่อน แล้ว push ไฟล์บัญชีจาก zip กลับเข้าเครื่อง (ต้องเปิด root มาก่อน)"""
    name = os.path.basename(zpath)
    M.log(serial, f"=== RESTORE {name} ===", Fore.GREEN)

    if not M.is_root(device):
        M.log(serial, "root ยังไม่เปิด → เปิด root ก่อน push", Fore.YELLOW)
        device = M.enable_root(device)
    if not M.is_root(device):
        M.log(serial, "✗ เปิด root ไม่ได้ → ยกเลิก restore", Fore.RED)
        return False

    # 1) ลบข้อมูลเดิมก่อน (clean เหมือน main.py) แล้วค่อย push ของใหม่
    device.shell(f"am force-stop {C.PACKAGE}")
    time.sleep(1)
    if step_on("clean"):
        M.delete_account_files(device)

    uid = get_app_uid(device)
    if not uid:
        M.log(serial, "✗ หา uid ของแอพไม่ได้ → ยกเลิก restore", Fore.RED)
        return False
    M.log(serial, f"app uid = {uid}", Fore.CYAN)

    # 2) แตก zip แล้ว push แต่ละไฟล์กลับ path เดิม
    tmp = os.path.join(tempfile.gettempdir(),
                       "cr_login_" + serial.replace(".", "_").replace(":", "_"))
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)
    except Exception as e:
        M.log(serial, f"✗ แตก zip ไม่ได้: {e}", Fore.RED)
        shutil.rmtree(tmp, ignore_errors=True)
        return False

    all_ok = True
    pushed = 0
    for fname in os.listdir(tmp):
        local = os.path.join(tmp, fname)
        if not os.path.isfile(local):
            continue
        dest_dir, mode = target_for(fname)
        if dest_dir is None:
            M.log(serial, f"  ข้าม (ไม่รู้ปลายทาง): {fname}", Fore.YELLOW)
            continue
        if push_into(device, serial, local, fname, dest_dir, mode, uid):
            M.log(serial, f"  ส่งแล้ว ✓ {fname}", Fore.GREEN)
            pushed += 1
        else:
            M.log(serial, f"  ส่งไม่ได้ ✗ {fname}", Fore.RED)
            all_ok = False
    shutil.rmtree(tmp, ignore_errors=True)
    return all_ok and pushed > 0


# ═══════════════════════════════════════════════════════════════════════
#  EXPORT — ดึงไฟล์บัญชีจากเครื่อง แล้ว zip เก็บใน login-success/
#  (อารมณ์เดียวกับ export_backup_zip ตอนเจอ id — แค่ปลายทางคนละที่ + ตั้งชื่อตามไฟล์ต้นทาง)
# ═══════════════════════════════════════════════════════════════════════
_zip_lock = threading.Lock()


def reserve_out_path(out_dir, base):
    """จองชื่อไฟล์ที่ว่างใน out_dir แบบ atomic: base.zip, base_2.zip, ..."""
    with _zip_lock:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{base}.zip")
        i = 2
        while os.path.exists(path):
            path = os.path.join(out_dir, f"{base}_{i}.zip")
            i += 1
        open(path, "wb").close()   # จองชื่อไว้ก่อน
        return path


def export_login_zip(device, out_name, out_dir):
    """ดึง shared_prefs + files ทั้งหมด แล้ว zip เก็บใน out_dir/out_name.zip"""
    serial = device.serial
    os.makedirs(out_dir, exist_ok=True)
    # staging ไว้ใน temp ของระบบ (ไม่ใช่ใน out_dir) → ไม่มีขยะ _tmp ค้างปนไฟล์ผลลัพธ์
    tmp_dir = tempfile.mkdtemp(prefix="cklogin_")
    try:
        pulled = []
        for f in C.SHARED_PREFS_FILES:
            local = os.path.join(tmp_dir, f)
            if M.pull_file(serial, f"{C.SHARED_PREFS_DIR}/{f}", local):
                pulled.append((local, f))
                M.log(serial, f"  pulled prefs: {f}", Fore.GREEN)
            else:
                M.log(serial, f"  ⚠️ pull ล้มเหลว: {f}", Fore.YELLOW)
        for f in C.FILES_FILES:
            local = os.path.join(tmp_dir, f)
            if M.pull_file(serial, f"{C.FILES_DIR}/{f}", local):
                pulled.append((local, f))
                M.log(serial, f"  pulled files: {f}", Fore.GREEN)
            else:
                M.log(serial, f"  ⚠️ pull ล้มเหลว: {f}", Fore.YELLOW)

        if not pulled:
            M.log(serial, "ไม่มีไฟล์ให้ zip → export ล้มเหลว", Fore.RED)
            return None

        zip_path = reserve_out_path(out_dir, out_name)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for local, arc in pulled:
                zf.write(local, arc)
        M.log(serial, f"✅ export → {os.path.join(os.path.basename(out_dir), os.path.basename(zip_path))}", Fore.GREEN)
        return zip_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)   # ลบ staging เสมอ แม้จะ error กลางทาง


# ═══════════════════════════════════════════════════════════════════════
#  ย้ายไฟล์ต้นทาง (done / failed)
# ═══════════════════════════════════════════════════════════════════════
def move_zip(zpath, dest_dir):
    try:
        os.makedirs(dest_dir, exist_ok=True)
        base, ext = os.path.splitext(os.path.basename(zpath))
        dest = os.path.join(dest_dir, base + ext)
        i = 2
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{base}_{i}{ext}")
            i += 1
        shutil.move(zpath, dest)
        return dest
    except Exception as e:
        print(f"{Fore.YELLOW}[WARN] ย้ายไฟล์ {os.path.basename(zpath)} ไม่ได้: {e}{Style.RESET_ALL}")
        return None


# ═══════════════════════════════════════════════════════════════════════
#  STEP: maxpet — get-pet เวอร์ชัน login (หลังกด pet1 → swipe 5 รอบ ก่อนหา pet2)
#  ตรรกะ match/break เหมือน main.run_get_pet: weak (RECORD_ALONE) จดแล้ววนต่อ,
#  strong (trader) เจอแล้วจบ — แยกไว้ที่นี่เพื่อไม่แตะ get-pet ของ main.py
# ═══════════════════════════════════════════════════════════════════════
PET_SWIPE = (279, 348, 737, 334)   # (x1,y1)→(x2,y2) ท่า swipe หลังกด pet1
PET_SWIPE_ROUNDS = 5               # กี่รอบ
PET_SWIPE_MS = 300                 # เวลาลาก 1 รอบ (มิลลิวินาที)


def run_maxpet(device, found):
    serial = device.serial
    M.log(serial, "=== MAX-PET (login) ===", Fore.GREEN)

    M.wait_and_click(device, "pet1.bmp", post_delay=1.5)

    # หลังกด pet1 → swipe (279,348)→(737,334) 5 รอบ ก่อนไปหา pet2
    x1, y1, x2, y2 = PET_SWIPE
    for i in range(PET_SWIPE_ROUNDS):
        if not M.bot_running:
            return
        M.log(serial, f"swipe {i+1}/{PET_SWIPE_ROUNDS} ({x1},{y1})→({x2},{y2})", Fore.CYAN)
        device.shell(f"input swipe {x1} {y1} {x2} {y2} {PET_SWIPE_MS}")
        time.sleep(0.5)

    M.wait_and_click(device, "pet2.bmp", post_delay=1.5)

    start = time.time()
    while time.time() - start < C.LOOP_MAX_SECS:
        if not M.bot_running:
            return
        img = M.fast_screencap(device)
        if img is None:
            time.sleep(0.3)
            continue

        # math เพ็ทที่สุ่มได้ → จดไว้ (ไม่ clear) แล้ววนต่อ | strong (trader) → จบเลย
        pet_hit = False
        for fname, name in C.PET_GET_MAP.items():
            if name in found:
                continue
            if M.ImgSearchADB(img, M.img_path(fname, C.PET_GET_DIR), C.ITEM_MATCH_THRESHOLD):
                found.add(name)
                strong = (name not in C.RECORD_ALONE) or (C.RECORD_ALONE.get(name) is True)
                M.log(serial, f"⭐ maxpet เจอ: {name} ({fname})"
                              + (" → จบ" if strong else " (วนต่อ)"), Fore.GREEN)
                if strong:
                    pet_hit = True
        if pet_hit:
            break

        # end-pet → จบ (ไม่มีไฟล์ end-pet.bmp ก็ใช้ safety cap แทน)
        if M.ImgSearchADB(img, M.img_path("end-pet.bmp")):
            M.log(serial, "เจอ end-pet → จบ maxpet")
            break

        pts3 = M.ImgSearchADB(img, M.img_path("pet3.bmp"))
        if pts3:
            M.tap(device, *pts3[0])
            time.sleep(0.6)
        pts4 = M.ImgSearchADB(img, M.img_path("pet4.bmp"))
        if pts4:
            M.tap(device, *pts4[0])
            time.sleep(3)
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════════════════════
#  STEP: event — event loop เวอร์ชัน login (appear timeout สั้นลง ให้ข้ามไว)
#  ใช้ handle_repeating ของ main.py แต่ override appear_timeout = EVENT_APPEAR_TIMEOUT
#  (default ใน main ถูก bind ตอน import แล้ว → แก้ C.APPEAR_TIMEOUT ทีหลังไม่มีผล
#   จึงต้องส่ง appear_timeout ตรงๆ ที่นี่)
# ═══════════════════════════════════════════════════════════════════════
EVENT_APPEAR_TIMEOUT = 3   # รอรูป event โผล่ครั้งแรกกี่วิ (ไม่โผล่ → ข้าม)
EVENT_NAMES = ("event-back.bmp", "git-item.bmp", "ok-gifitem.bmp", "fixnews.bmp")

EVENT_CHECKPOINT = "check-pointevent.bmp"   # รูป checkpoint ก่อนเข้าหน้า event
EVENT_CHECKPOINT_TIMEOUT = 60               # รอ checkpoint กี่วิ (ไม่เจอ → เริ่ม event เลย)


def wait_event_checkpoint(device):
    """รอ check-pointevent.bmp โผล่ก่อนเริ่ม EVENT LOOP (เจอ = เกมโหลดถึงหน้า event แล้ว)
    ไม่เจอใน timeout → เริ่ม event เลย (กันค้าง)"""
    serial = device.serial
    path = M.img_path(EVENT_CHECKPOINT)
    M.log(serial, f"รอ checkpoint event ({EVENT_CHECKPOINT})...", Fore.CYAN)
    start = time.time()
    while time.time() - start < EVENT_CHECKPOINT_TIMEOUT:
        if not M.bot_running:
            return False
        img = M.fast_screencap(device)
        if M.ImgSearchADB(img, M.img_path(LOGIN_FAILED_IMG)):
            raise LoginFailed()
        if M.ImgSearchADB(img, path):
            M.log(serial, "เจอ checkpoint event → เริ่ม EVENT LOOP", Fore.GREEN)
            return True
        time.sleep(0.3)
    M.log(serial, f"ไม่เจอ checkpoint event ใน {EVENT_CHECKPOINT_TIMEOUT}s → เริ่ม EVENT LOOP เลย", Fore.YELLOW)
    return False


def run_event_loops(device):
    serial = device.serial
    for rnd in range(1, C.EVENT_LOOP_ROUNDS + 1):
        _raise_if_login_failed(device)   # เจอ login-failed ระหว่าง event → ยกเลิกบัญชี
        M.log(serial, f"=== EVENT LOOP รอบ {rnd}/{C.EVENT_LOOP_ROUNDS} ===", Fore.GREEN)
        for name in EVENT_NAMES:
            M.handle_repeating(device, name, appear_timeout=EVENT_APPEAR_TIMEOUT)


# ═══════════════════════════════════════════════════════════════════════
#  STEP: box — รับของ box เวอร์ชัน login
#  box1 → box2 → box3 (timeout 15s) : ไม่เจอ box3 ใน 15 วิ → ข้ามไป box5 แล้วจบ step
#                                     เจอ box3 → box4 → box5 ตามปกติ
#  (แยกไว้ที่นี่เพื่อไม่แตะ run_boxes ของ main.py)
# ═══════════════════════════════════════════════════════════════════════
BOX3_TIMEOUT = 15   # ไม่เจอ box3 กี่วิ ให้ข้ามไป box5 แล้วจบ


def run_boxes(device):
    serial = device.serial
    M.log(serial, "=== รับของ BOX (login) ===", Fore.GREEN)

    M.wait_and_click(device, "box1.bmp", post_delay=1.5)
    M.wait_and_click(device, "box2.bmp", post_delay=1.5)

    # ไม่เจอ box3 ใน BOX3_TIMEOUT วิ → ข้ามไป box5 แล้วจบ step เลย
    if not M.wait_and_click(device, "box3.bmp", timeout=BOX3_TIMEOUT, required=False, post_delay=1.5):
        M.log(serial, f"ไม่เจอ box3 ครบ {BOX3_TIMEOUT} วิ → ข้ามไป box5 แล้วจบ box", Fore.YELLOW)
        M.wait_and_click(device, "box5.bmp", post_delay=1.5)
        return

    # เจอ box3 → box4 → box5 ตามปกติ
    for i in range(4, 6):
        M.wait_and_click(device, f"box{i}.bmp", post_delay=1.5)


# ═══════════════════════════════════════════════════════════════════════
#  STEP: maxgacha — สุ่มกาชาแบบ item (template อยู่ใน img/max-gacha/)
#  ลำดับ:
#   maxgacha1 → disk-full → maxgacha2
#   → maxgacha3 (15วิ)?  เจอ  : วน (maxgacha-step1 + สแกน ITEM) จนไม่เจอ maxgacha3 15วิ
#                         ไม่เจอ: maxgacha4 → disk-full → maxgacha5 (รัวจนหาย 5วิ)+สแกน ITEM  [#step-ruby]
#   → draw-agin loop: draw-agin → disk-full → ok-get (รัวจนหาย 5วิ) วนจนเจอ stop-ruby → cancel1 → cancel2
#   → step2: get-random25 → disk-full → ok-getstep2 (รัวจนเจอ stop-step2) → cancel-step2 (+v1/v2/v3)
#  จดชื่อ ITEM_GET_MAP ที่ math ได้ลง found (เอาไปตั้งชื่อไฟล์ตอน export)
#  ⚠️ ต้องเพิ่มรูป maxgacha-step1.bmp + stop-ruby.bmp (ยังไม่มี — ตอนนี้จะข้าม/พึ่ง safety cap)
# ═══════════════════════════════════════════════════════════════════════
MAXGACHA_DIR = "img/max-gacha"


def _mg_click(device, name, timeout=C.PLAY_STEP_TIMEOUT, post_delay=1.2):
    """คลิกรูปในโฟลเดอร์ max-gacha (required=False → ไม่เจอก็ข้าม)"""
    return M.wait_and_click(device, name, timeout=timeout, required=False,
                            post_delay=post_delay, folder=MAXGACHA_DIR)


def _mg_disk_full(device):
    """แวะหา disk-full1 (5วิ) ไม่เจอข้าม; เจอ → disk-full2 → disk-full3 → fixdisk1 → fixdisk2"""
    if _mg_click(device, "disk-full1.bmp", timeout=5):
        _mg_click(device, "disk-full2.bmp", timeout=10)
        _mg_click(device, "disk-full3.bmp", timeout=10)
        _mg_click(device, "fixdisk1.bmp", timeout=10)
        _mg_click(device, "fixdisk2.bmp", timeout=10)


def _mg_scan_items(device, found, img=None):
    """สแกน ITEM_GET_MAP บนจอ → จดชื่อที่ match ลง found (ไม่ clear ของเดิม)"""
    if img is None:
        img = M.fast_screencap(device)
    if img is None:
        return
    for fname, name in C.ITEM_GET_MAP.items():
        if name in found:
            continue
        if M.ImgSearchADB(img, M.img_path(fname, C.ITEM_GET_DIR), C.ITEM_MATCH_THRESHOLD):
            found.add(name)
            M.log(device.serial, f"⭐ maxgacha เจอ: {name} ({fname})", Fore.GREEN)


def _mg_scan_items_window(device, found, secs=2.0, interval=0.35):
    """เว้นช่วง secs วิ แล้วสแกน ITEM_GET_MAP ต่อเนื่องหลายเฟรม (กัน popup ขึ้นแวบเดียวแล้วหาพลาด)
    → จับให้ชัวร์ที่สุดในการจดชื่อของที่สุ่มได้"""
    end = time.time() + secs
    while M.bot_running and time.time() < end:
        _mg_scan_items(device, found)
        time.sleep(interval)


def _mg_spam_until_gone(device, name, absent=5, found=None):
    """กด name รัวๆ จนไม่เจอติดต่อกัน absent วิ (สแกน ITEM ระหว่างวนถ้าส่ง found)"""
    path = M.img_path(name, MAXGACHA_DIR)
    last_seen = time.time()
    while M.bot_running and time.time() - last_seen < absent:
        img = M.fast_screencap(device)
        if found is not None:
            _mg_scan_items(device, found, img)
        pts = M.ImgSearchADB(img, path)
        if pts:
            M.tap(device, *pts[0])
            last_seen = time.time()
        time.sleep(0.4)


def _mg_draw_again(device):
    """draw-agin → disk-full → ok-get (รัวจนหาย 5วิ) วนไปจนเจอ stop-ruby → cancel1 → cancel2"""
    serial = device.serial
    M.log(serial, "--- draw-agin loop ---", Fore.MAGENTA)
    start = time.time()
    while M.bot_running and time.time() - start < C.LOOP_MAX_SECS:
        img = M.fast_screencap(device)
        # เจอ stop-step2 → cancel-step2 → cancel-step2v1 → break ออกไป get-random25 (step2) เลย
        if M.ImgSearchADB(img, M.img_path("stop-step2.bmp", MAXGACHA_DIR)):
            M.log(serial, "เจอ stop-step2 → cancel-step2 → cancel-step2v1 → ไป get-random25", Fore.GREEN)
            _mg_click(device, "cancel-step2.bmp")
            _mg_click(device, "cancel-step2v1.bmp")
            return
        # เจอ stop-ruby → cancel1 → cancel2
        if M.ImgSearchADB(img, M.img_path("stop-ruby.bmp", MAXGACHA_DIR)):
            M.log(serial, "เจอ stop-ruby → cancel1 → cancel2", Fore.GREEN)
            _mg_click(device, "cancel1.bmp")
            _mg_click(device, "cancel2.bmp")
            return
        if _mg_click(device, "draw-agin.bmp", timeout=5):
            _mg_disk_full(device)
            _mg_spam_until_gone(device, "ok-get.bmp", absent=5)
        else:
            break   # ไม่เจอ draw-agin แล้ว → ออก
    M.log(serial, "จบ draw-agin loop", Fore.CYAN)


def _mg_step2(device, found=None, absent=8):
    """get-random25 → disk-full → ok-getstep2 (รัวจนเจอ stop-step2) → cancel-step2 (+v1/v2/v3)
    ออกจากลูปเมื่อ: เจอ stop-step2 | ไม่เจอ ok-getstep2/stop-step2 ครบ absent วิ | ชน LOOP_MAX_SECS
    ระหว่างวนสแกน ITEM_GET_MAP ทุกเฟรม + เว้นจังหวะ 2 วิ หลังกดรับของ (กันหาพลาด/ไม่จด)"""
    serial = device.serial
    stop_path = M.img_path("stop-step2.bmp", MAXGACHA_DIR)
    M.log(serial, "=== STEP2 ===", Fore.GREEN)
    if not _mg_click(device, "get-random25.bmp", timeout=15):
        M.log(serial, "ไม่เจอ get-random25 → กด fix-random25 แล้วหา get-random25 ใหม่", Fore.YELLOW)
        _mg_click(device, "fix-random25.bmp", timeout=10)
        if not _mg_click(device, "get-random25.bmp", timeout=15):
            M.log(serial, "⚠️ ยังไม่เจอ get-random25 หลัง fix-random25 (หน้า step2 อาจยังไม่เปิด)", Fore.YELLOW)
    _mg_disk_full(device)

    start = last_action = time.time()
    clicks = 0
    while M.bot_running and time.time() - start < C.LOOP_MAX_SECS:
        img = M.fast_screencap(device)
        if found is not None:
            _mg_scan_items(device, found, img)   # สแกน ITEM ทุกเฟรม
        if M.ImgSearchADB(img, stop_path):
            M.log(serial, f"เจอ stop-step2 (กด ok-getstep2 ไป {clicks} ครั้ง) → cancel-step2", Fore.GREEN)
            break
        pts = M.ImgSearchADB(img, M.img_path("ok-getstep2.bmp", MAXGACHA_DIR))
        if pts:
            M.tap(device, *pts[0])
            clicks += 1
            last_action = time.time()
            # เว้นจังหวะ 2 วิ หา ITEM_GET_MAP หลังกดรับของ (เจอ stop-step2 ระหว่างนั้น → ออกเลย)
            if found is not None:
                wend = time.time() + 2.0
                while M.bot_running and time.time() < wend:
                    wimg = M.fast_screencap(device)
                    _mg_scan_items(device, found, wimg)
                    if M.ImgSearchADB(wimg, stop_path):
                        break
                    time.sleep(0.35)
        elif time.time() - last_action > absent:
            M.log(serial, f"ไม่เจอ ok-getstep2/stop-step2 ครบ {absent}s (กดไป {clicks} ครั้ง) → จบ step2", Fore.YELLOW)
            break
        time.sleep(0.4)
    for n in ("cancel-step2.bmp", "cancel-step2v1.bmp", "cancel-step2v2.bmp", "cancel-step2v3.bmp"):
        _mg_click(device, n)


def _mg_stop_step2_jump(device, timeout=3):
    """เจอ stop-step2 ภายใน timeout → กด cancel-step2 → cancel-step2v1 แล้วคืน True (ให้ข้ามไป step2)"""
    path = M.img_path("stop-step2.bmp", MAXGACHA_DIR)
    start = time.time()
    while M.bot_running and time.time() - start < timeout:
        if M.ImgSearchADB(M.fast_screencap(device), path):
            M.log(device.serial, "เจอ stop-step2 → cancel-step2 → cancel-step2v1 → ไป get-random25", Fore.GREEN)
            _mg_click(device, "cancel-step2.bmp")
            _mg_click(device, "cancel-step2v1.bmp")
            return True
        time.sleep(0.3)
    return False


def run_maxgacha(device, found):
    serial = device.serial
    M.log(serial, "=== MAX-GACHA ===", Fore.GREEN)

    _mg_click(device, "maxgacha1.bmp")
    _mg_disk_full(device)
    _mg_click(device, "maxgacha2.bmp")

    if _mg_click(device, "maxgacha3.bmp", timeout=15):
        # เจอ maxgacha3 → วน (maxgacha-step1 + สแกน ITEM) จนไม่เจอ maxgacha3 15วิ
        while M.bot_running:
            _mg_scan_items_window(device, found, secs=2.0)   # เว้น 2 วิ หา ITEM ให้ชัวร์ก่อนกด
            _mg_click(device, "maxgacha-step1.bmp", timeout=10)   # ⚠️ ยังไม่มีรูปนี้
            _mg_scan_items(device, found)
            if not _mg_click(device, "maxgacha3.bmp", timeout=15):
                break
        _mg_scan_items_window(device, found, secs=2.0)   # กวาดปิดท้ายอีกรอบกันของค้างบนจอ
        M.log(serial, "จบลูป maxgacha3 → ไปต่อ", Fore.CYAN)
    else:
        # ไม่เจอ maxgacha3 → maxgacha4 → #step-ruby (maxgacha5 loop)
        M.log(serial, "ไม่เจอ maxgacha3 → maxgacha4 (#step-ruby)", Fore.YELLOW)
        _mg_click(device, "maxgacha4.bmp", timeout=15)
        # กด maxgacha4 แล้วเจอ stop-step2 → cancel → ข้ามไป get-random25 (step2) เลย
        if _mg_stop_step2_jump(device):
            _mg_step2(device, found)
            return
        _mg_disk_full(device)
        _mg_spam_until_gone(device, "maxgacha5.bmp", absent=5, found=found)

    # common tail
    _mg_draw_again(device)
    _mg_step2(device, found)


# ═══════════════════════════════════════════════════════════════════════
#  ตัดสินชื่อไฟล์ + โฟลเดอร์ปลายทางตอน export
#  กติกา (trader เป็นหลัก):
#    - มี trader (สุ่มได้รอบนี้ หรือมีในชื่อไฟล์เดิมอยู่แล้ว) → backup-id
#      ชื่อ = รวมทุกชิ้น (ของเดิม + เพ็ทที่สุ่มได้) เรียงตาม ITEM→PET เช่น banana+trader+[ID]+
#    - maxpet เปิดแต่ไม่มี trader → random-Fail (ชื่อเดิม)
#    - maxpet ปิด (ไม่ได้สุ่ม) และไม่มี trader → login-success (ชื่อเดิม)
# ═══════════════════════════════════════════════════════════════════════
def _split_orig_name(base):
    """แยกชื่อไฟล์เดิม → (list ชื่อของก่อน [ID], ส่วน [ID] ท้าย)
    เช่น 'banana+[BCVZL1719]+' → (['banana'], '[BCVZL1719]+')"""
    i = base.find("[")
    if i == -1:
        prefix, id_suffix = base.rstrip("+"), ""
    else:
        prefix, id_suffix = base[:i].rstrip("+"), base[i:]
    pieces = [p for p in prefix.split("+") if p]
    return pieces, id_suffix


def _combine_name(base, found):
    """เติมชื่อที่เจอ (found) หน้าชื่อไฟล์เดิม เรียง canonical (ITEM ก่อน PET)
    เช่น base='headking+[ID]+', found={backpack,banana} → 'backpack+banana+headking+[ID]+'
    ถ้าไม่มีของใหม่ (found ⊆ ของเดิม) → คงชื่อเดิม"""
    orig_pieces, id_suffix = _split_orig_name(base)
    new_pieces = [p for p in found if p not in orig_pieces]
    if not new_pieces:
        return base
    all_set = set(orig_pieces) | set(found)
    order = M.ordered_names()
    name_pieces = [n for n in order if n in all_set]
    for p in orig_pieces:
        if p not in order and p not in name_pieces:
            name_pieces.append(p)
    prefix = "+".join(name_pieces)
    return f"{prefix}+{id_suffix}" if id_suffix else prefix


def decide_login_export(base, found, maxpet_on):
    """คืน (out_name, out_dir) ตามกติกา trader (ใช้เส้นทาง maxpet)"""
    orig_pieces, _ = _split_orig_name(base)
    if "trader" in (set(orig_pieces) | set(found)):
        return _combine_name(base, found), LOGIN["backup_id_dir"]
    if maxpet_on:
        return base, LOGIN["random_fail_dir"]
    return base, LOGIN["output_dir"]


# ตัวคั่นในป้ายนับใช้ '-' (เช่น (2-2)) — '/' ปกติใช้ในชื่อไฟล์ Windows ไม่ได้ (ตัวคั่น path)
_CNT_SEP = "-"
_COUNT_PREFIX_RE = re.compile(r"^\(\d+[-/／]\d+\)\+")   # จับทั้ง '-' ใหม่ และ '/'、'／' เก่า เวลาตัด prefix ซ้ำ


def _count_prefix(name):
    """เติมป้ายนับหน้าชื่อไฟล์ ตามว่ามี "item หลัก" ในชื่อไหม
    item หลัก = อยู่ใน ITEM_GET_MAP และไม่อยู่ใน RECORD_ALONE (strong เช่น headking)
      ของอ่อน (backpack/sturdy-glove/banana... ที่อยู่ใน RECORD_ALONE) ไม่นับเป็นหลัก
      มี item หลัก (เช่น headking+trader ตำแหน่งไหนก็ได้)        → (2-2)+headking+trader   = ครบ
      มีแต่เพ็ท/trader หรือมีแต่ของอ่อน ไม่มี item หลัก           → (0-1)+backpack+trader
    (ตัด prefix (x-y) เดิมออกก่อน กันซ้ำเวลา re-process)"""
    name = _COUNT_PREFIX_RE.sub("", name)
    pieces, _ = _split_orig_name(name)
    main_items = set(C.ITEM_GET_MAP.values()) - set(C.RECORD_ALONE)   # item หลักจริง (strong)
    has_main = any(p in main_items for p in pieces)
    num = f"2{_CNT_SEP}2" if has_main else f"0{_CNT_SEP}1"
    return f"({num})+{name}"


def _shard_dir(base_dir):
    """คืนโฟลเดอร์ย่อย part-XXXX ใน base_dir ที่ยังมีไฟล์ < shard_size
    กันไฟล์กระจุกในโฟลเดอร์เดียวเป็นพันจน Explorer ค้าง (shard_size=0 → ไม่แบ่ง คืน base_dir เดิม)"""
    size = LOGIN.get("shard_size", 500)
    os.makedirs(base_dir, exist_ok=True)
    if not size or size <= 0:
        return base_dir
    # เริ่มจาก part สูงสุดที่มีอยู่ (ไม่ต้องไล่นับจาก 1 ทุกครั้ง)
    existing = [e.name[5:] for e in os.scandir(base_dir)
                if e.is_dir() and e.name.startswith("part-") and e.name[5:].isdigit()]
    i = max((int(n) for n in existing), default=1)
    while True:
        d = os.path.join(base_dir, f"part-{i:04d}")
        os.makedirs(d, exist_ok=True)
        n = sum(1 for e in os.scandir(d) if e.name.endswith(".zip"))
        if n < size:
            return d
        i += 1


# ═══════════════════════════════════════════════════════════════════════
#  login-failed watchdog — เจอหน้า login-failed เมื่อไหร่ → ยกเลิกบัญชีนี้
#  clear app → export บัญชี (ชื่อเดิม) เข้า login-failed/ แล้วไป id ถัดไป
# ═══════════════════════════════════════════════════════════════════════
LOGIN_FAILED_IMG = "login-failed.bmp"


class LoginFailed(Exception):
    """โยนเมื่อเจอหน้า login-failed → process_account จับแล้วจัดการ"""


def login_failed_seen(device):
    return bool(M.ImgSearchADB(M.fast_screencap(device), M.img_path(LOGIN_FAILED_IMG)))


def _raise_if_login_failed(device):
    if login_failed_seen(device):
        raise LoginFailed()


def handle_login_failed(device, serial, base):
    """clear app → export บัญชี (ชื่อเดิม) เข้า login-failed/ (input จัดการโดย worker)"""
    M.log(serial, "⚠️ เจอ login-failed → clear app → เก็บเข้า login-failed/ (ชื่อเดิม)", Fore.RED)
    M.close_app(device)
    device = M.enable_root(device)
    export_login_zip(device, base, _shard_dir(LOGIN["login_failed_dir"]))
    device = M.disable_root(device)


# ═══════════════════════════════════════════════════════════════════════
#  ทำงาน 1 บัญชี : restore → start → event → box → maxgacha → export
#  (ระหว่าง login เจอ login-failed → เก็บเข้า login-failed/ แล้วไป id ถัดไป)
# ═══════════════════════════════════════════════════════════════════════
def process_account(device, serial, zpath):
    name = os.path.basename(zpath)
    base = os.path.splitext(name)[0]
    M.log(serial, f"┌─ เริ่มบัญชี: {name}", Fore.MAGENTA)

    # 1) restore (เปิด root ตลอดช่วง push แล้วค่อยปิด)
    if step_on("restore"):
        device = M.enable_root(device)
        ok = restore_account(device, serial, zpath)
        device = M.disable_root(device)
        if not ok:
            M.log(serial, f"└─ restore ล้มเหลว → ข้าม {name}", Fore.RED)
            return False
    else:
        M.log(serial, "ข้าม restore (ปิดใน config)", Fore.YELLOW)

    # 2) start packet (root ปิดอยู่ → เกมไม่เจอ root)
    M.start_game(device)
    time.sleep(LOGIN["start_wait"])

    found = set()
    try:
        _raise_if_login_failed(device)   # เจอ login-failed ตั้งแต่หลัง start → ยกเลิก

        # 3) event loops — รอ checkpoint event ให้เจอก่อน ค่อยเริ่ม EVENT LOOP
        if step_on("event"):
            wait_event_checkpoint(device)
            run_event_loops(device)
        M.log(serial, "login เสร็จ → ทำ config เพิ่ม (box / maxgacha)", Fore.CYAN)

        # 4) box — box1 → box2 → box3 (ไม่เจอ 15 วิ → กด box5 แล้วจบ) → box4-5 (ถ้า box=1)
        if step_on("box"):
            run_boxes(device)

        # 5) maxgacha — สุ่มกาชา item (ถ้า maxgacha=1) จดชื่อ item ที่ math ได้ลง found
        if step_on("maxgacha"):
            run_maxgacha(device, found)

        # 6) maxpet — กด pet1 → swipe 5 รอบ → สุ่มเพ็ทจนเจอ trader (ถ้า maxpet=1)
        if step_on("maxpet"):
            run_maxpet(device, found)
    except LoginFailed:
        handle_login_failed(device, serial, base)
        return True

    # 7) export — ตัดสินชื่อ/ปลายทาง (ทำหลัง step2 → ไม่ clear app ก่อนถึง step2)
    #    maxgacha: เจอ item → backup-id (ชื่อ = item + เดิม) | สุ่มไม่ได้อะไรเลย → random-Fail (ชื่อเดิม)
    #    ไม่งั้น → กติกา trader (decide_login_export)
    if step_on("export"):
        if step_on("maxgacha"):
            if found:
                out_name, out_dir = _combine_name(base, found), LOGIN["backup_id_dir"]
            else:
                out_name, out_dir = base, LOGIN["random_fail_dir"]
        elif step_on("maxpet"):
            out_name, out_dir = decide_login_export(base, found, True)
        else:
            out_name, out_dir = base, LOGIN["output_dir"]
        if out_dir == LOGIN["backup_id_dir"]:
            out_name = _count_prefix(out_name)   # เติม (0/N) เฉพาะไฟล์ที่เข้า backup-id
        out_dir = _shard_dir(out_dir)   # แบ่งเป็น part-XXXX กันไฟล์กระจุกจน Explorer ค้าง
        M.log(serial, f"→ เก็บ {out_name}.zip ใน {out_dir}/", Fore.GREEN)

        M.close_app(device)
        device = M.enable_root(device)
        out = export_login_zip(device, out_name, out_dir)
        device = M.disable_root(device)
        if out is None:
            M.log(serial, f"└─ export ล้มเหลว → เก็บ {name} ไว้ที่เดิม", Fore.RED)
            return False
    else:
        M.log(serial, "ข้าม export (ปิดใน config)", Fore.YELLOW)

    M.log(serial, f"└─ เสร็จบัญชี: {name}", Fore.GREEN)
    return True


# ═══════════════════════════════════════════════════════════════════════
#  MAIN — แต่ละเครื่อง "claim" zip จาก input-id/ แบบ atomic (กันแย่งไฟล์เดียวกัน)
#
#  วิธี claim: lock file (os.open O_CREAT|O_EXCL) เป็นตัวตัดสิน แล้วย้ายไฟล์เข้า
#  input-id/_processing/<serial>/  (ดูรายละเอียดใน claim_next_zip)
#  - O_EXCL atomic จริงบน Windows (os.rename ไม่ atomic ตอน race — ทดสอบแล้ว)
#  - กันได้ทั้ง "หลาย thread ในโปรเซสเดียว" และ "หลายโปรเซส/เปิดหลายหน้าต่างพร้อมกัน"
#  - ไฟล์ที่ claim อยู่ใน _processing/<serial>/ จนกว่าจะเสร็จ → เห็นชัดว่าเครื่องไหนถืออะไร
#    ถ้า crash กลางคัน ไฟล์ค้างอยู่ตรงนั้น รอบหน้าเครื่องเดิมหยิบมาทำต่อได้ (recover)
# ═══════════════════════════════════════════════════════════════════════
STATS = {"done": 0, "fail": 0}
STATS_LOCK = threading.Lock()


def claim_dir_for(serial):
    """โฟลเดอร์ที่จองไฟล์ของเครื่องนี้โดยเฉพาะ (1 serial = 1 โฟลเดอร์ ไม่ปนกัน)"""
    safe = serial.replace(".", "_").replace(":", "_")
    d = os.path.join(LOGIN["claim_dir"], safe)
    os.makedirs(d, exist_ok=True)
    return d


def claim_next_zip(input_dir, my_claim, stale_after=30):
    """หยิบ zip ตัวถัดไปแบบ atomic — คืน path ใหม่ใน my_claim หรือ None ถ้าไม่มีเหลือ

    ตัวตัดสินว่าใครได้ไฟล์ = lock file สร้างด้วย os.open(O_CREAT|O_EXCL):
      - O_EXCL = CREATE_NEW บน Windows → atomic จริง (ทดสอบแล้ว)
        NB: os.rename บน Windows "ไม่" atomic ตอน race — เคยเจอไฟล์เดียวถูก claim ซ้ำ
      - ใครสร้าง _locks/<zip>.lock สำเร็จ = ได้สิทธิ์ย้ายไฟล์ (ถือ lock แค่ช่วง rename แล้วลบ)
      - lock ค้างเกิน stale_after วิ (เจ้าของ crash คา) → ยึดมาใหม่ได้
    """
    # lock ไฟล์เก็บในโฟลเดอร์ย่อย _locks/ (ไม่ปนใน input-id → Explorer ไม่โดน churn จาก lock)
    lock_dir = os.path.join(input_dir, "_locks")
    os.makedirs(lock_dir, exist_ok=True)
    while True:
        found_any = False
        # os.scandir แบบ lazy: หยุดทันทีที่ claim ไฟล์แรกได้ (ไม่ต้องสแกน/เรียงครบทุกไฟล์)
        for entry in os.scandir(input_dir):
            if not entry.name.endswith(".zip") or not entry.is_file():
                continue
            found_any = True
            lock = os.path.join(lock_dir, entry.name + ".lock")
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # อาจเป็น lock ค้างจาก process ที่ crash → เก่าเกินกำหนดก็ยึดมา
                try:
                    if time.time() - os.path.getmtime(lock) > stale_after:
                        os.remove(lock)
                except OSError:
                    pass
                continue                       # คนอื่นถืออยู่ → ลองตัวถัดไป
            except OSError:
                continue
            os.close(fd)
            try:
                dest = os.path.join(my_claim, entry.name)
                os.rename(entry.path, dest)    # ถือ lock อยู่คนเดียว → ย้ายปลอดภัย แล้วออกทันที
                return dest
            except OSError:
                continue                       # ไฟล์หายไปแล้ว → ลองตัวถัดไป
            finally:
                try:
                    os.remove(lock)            # ปลด lock (ไฟล์ถูกย้ายออกไปแล้ว)
                except OSError:
                    pass
        if not found_any:
            return None                        # ไม่มี .zip เหลือแล้ว
        time.sleep(0.05)   # มีไฟล์แต่โดน lock อยู่ชั่วขณะ → พักสั้นๆ แล้ววนใหม่


def _worker_loop(serial, input_dir):
    """ลูปหลักต่อ 1 เครื่อง: claim → process_account → ย้ายไฟล์ จนคิวหมด. คืน (done, fail)"""
    device = AdbClient(host="127.0.0.1", port=5037).device(serial)
    if device is None:
        M.log(serial, "ERROR: เชื่อมต่อ device ไม่ได้", Fore.RED)
        return 0, 0
    my_claim = claim_dir_for(serial)
    done = fail = 0

    while M.bot_running:
        # 1) เก็บงานค้างของ "เครื่องตัวเอง" ก่อน (เผื่อรอบก่อน crash ค้างใน _processing)
        leftovers = sorted(glob.glob(os.path.join(my_claim, "*.zip")))
        if leftovers:
            zpath = leftovers[0]
            M.log(serial, f"เจองานค้างของเครื่องนี้ → ทำต่อ: {os.path.basename(zpath)}", Fore.YELLOW)
        else:
            # 2) claim ไฟล์ใหม่จาก input-id แบบ atomic (กันเครื่อง/โปรเซสอื่นแย่ง)
            zpath = claim_next_zip(input_dir, my_claim)
            if zpath is None:
                break
            M.log(serial, f"claim: {os.path.basename(zpath)}", Fore.CYAN)

        try:
            ok = process_account(device, serial, zpath)
        except Exception as e:
            M.log(serial, f"Error ระหว่างทำ {os.path.basename(zpath)}: {e}", Fore.RED)
            ok = False

        # ย้ายไฟล์ที่ claim ไว้ ออกจาก _processing ตามผลลัพธ์ (ต้องย้ายออกเสมอ กันวนซ้ำ)
        if ok:
            done += 1
            try:
                os.remove(zpath)   # ไม่เก็บ _done — บัญชีถูก export แยกไป backup-id/random-fail/login-success แล้ว
            except OSError:
                pass
        else:
            fail += 1
            move_zip(zpath, LOGIN["failed_dir"])       # เก็บไว้ตรวจสอบ

    M.log(serial, "ไม่มีไฟล์เหลือให้ claim → จบการทำงานเครื่องนี้", Fore.GREEN)
    return done, fail


def worker(serial, input_dir):
    """เวอร์ชัน thread (ใช้โดย login-gui.py) — รวมผลลง STATS"""
    done, fail = _worker_loop(serial, input_dir)
    with STATS_LOCK:
        STATS["done"] += done
        STATS["fail"] += fail


# ═══════════════════════════════════════════════════════════════════════
#  multiprocess: 1 process / 1 เครื่อง — ลื่นสุดสำหรับหลายจอ (เลี่ยง GIL)
#  ระบบ claim ไฟล์ (lock) ทำงานข้าม process ได้อยู่แล้ว → พฤติกรรมเหมือนเดิม
# ═══════════════════════════════════════════════════════════════════════
def _stop_watcher(stop_event):
    """thread เล็กๆ ในแต่ละ process: parent สั่ง stop → ตั้ง M.bot_running=False
    (ลูปเดิมที่เช็ค bot_running จะหยุดเองหลังจบบัญชีปัจจุบัน)"""
    stop_event.wait()
    M.bot_running = False


def device_worker(serial, index, input_dir, stop_event, result_q):
    """entry ของแต่ละ process — ตั้งค่า ADB/root/priority ของตัวเอง แล้ววนทำงาน"""
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # ปล่อยให้ parent จัดการ Ctrl+C (ผ่าน stop_event)
    load_login_config()
    M.find_adb_executable()
    M.set_process_priority()                       # BELOW_NORMAL → สละ CPU ให้ UI ลื่น
    M.MUMU_MANAGER_PATH = M.find_mumu_manager() or ""
    if index:
        M.SERIAL_TO_INDEX[serial] = index          # ให้ root toggle ตรง instance ของตัวเอง
    M.bot_running = True
    threading.Thread(target=_stop_watcher, args=(stop_event,), daemon=True).start()
    M._adb_connect(serial)                          # ให้แน่ใจว่า adb ต่อเครื่องนี้อยู่

    done, fail = _worker_loop(serial, input_dir)
    result_q.put((serial, done, fail))


def _sweep_stale_tmp():
    """กวาดโฟลเดอร์ staging ที่ค้างจากรอบก่อน (เวอร์ชันเก่าเคยสร้าง _tmp_* ไว้ในโฟลเดอร์ผลลัพธ์)
    ไล่ลบ _tmp_* / cklogin_* ทั้งใน out_dir และ part-XXXX ย่อย → โฟลเดอร์ผลลัพธ์สะอาด"""
    out_dirs = [LOGIN["output_dir"], LOGIN["backup_id_dir"],
                LOGIN["random_fail_dir"], LOGIN["login_failed_dir"]]
    removed = 0
    for base in out_dirs:
        if not os.path.isdir(base):
            continue
        for d in glob.glob(os.path.join(base, "_tmp_*")) + \
                 glob.glob(os.path.join(base, "cklogin_*")) + \
                 glob.glob(os.path.join(base, "part-*", "_tmp_*")) + \
                 glob.glob(os.path.join(base, "part-*", "cklogin_*")):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    if removed:
        print(f"{Fore.CYAN}[CLEAN] ลบโฟลเดอร์ staging ค้าง {removed} อัน{Style.RESET_ALL}")


def main():
    load_login_config()
    M.set_process_priority()

    if not M.find_adb_executable():
        print(f"{Fore.RED}[ERROR] ไม่เจอ adb.exe{Style.RESET_ALL}")
        return

    _sweep_stale_tmp()   # เก็บกวาดขยะ _tmp ที่ค้างจากรอบก่อนก่อนเริ่ม
    input_dir = LOGIN["input_dir"]
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(LOGIN["claim_dir"], exist_ok=True)

    # นับงานที่ค้างทั้งหมด: ยังไม่ claim (input-id/*.zip) + ที่ claim ค้างไว้ (_processing/*/*.zip)
    pending = (glob.glob(os.path.join(input_dir, "*.zip"))
               + glob.glob(os.path.join(LOGIN["claim_dir"], "*", "*.zip")))
    if not pending:
        print(f"{Fore.RED}[ERROR] ไม่มี .zip ใน {input_dir}/ → วางไฟล์บัญชีก่อน{Style.RESET_ALL}")
        return

    M.bot_running = True
    devices = M.discover_devices()
    if not devices:
        print(f"{Fore.RED}[ERROR] ไม่เจอ device{Style.RESET_ALL}")
        return
    dev_idx = [(s, M.SERIAL_TO_INDEX.get(s, C.MUMU_INDEX)) for s in devices]
    print(f"{Fore.GREEN}[OK] เจอ {len(devices)} device: {devices} | รอทำ ~{len(pending)} บัญชี "
          f"(multiprocess แยกจอ){Style.RESET_ALL}")

    stop_event = mp.Event()
    result_q = mp.Queue()
    procs = []
    for serial, index in dev_idx:
        p = mp.Process(target=device_worker,
                       args=(serial, index, input_dir, stop_event, result_q))
        p.start()
        procs.append(p)
        time.sleep(2)   # stagger กัน adb/root toggle ชนกันตอนเริ่ม

    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        print(f"{Fore.YELLOW}[STOP] หยุด (รอบัญชีปัจจุบันของแต่ละจอจบก่อน)...{Style.RESET_ALL}")
        stop_event.set()
        for p in procs:
            p.join(timeout=180)
        for p in procs:
            if p.is_alive():
                p.terminate()

    done = fail = 0
    while True:
        try:
            _s, d, f = result_q.get_nowait()
            done += d
            fail += f
        except Exception:
            break
    print(f"{Fore.GREEN}[DONE] สำเร็จ {done} | ล้มเหลว {fail} | "
          f"ผลลัพธ์อยู่ใน {LOGIN['output_dir']}/{Style.RESET_ALL}")


if __name__ == "__main__":
    mp.freeze_support()   # ให้ spawn บน Windows ทำงานถูก (กัน re-run main ใน child)
    main()

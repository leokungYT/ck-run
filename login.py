"""
login.py — Cookie Run "login-refresh" แบบ batch
════════════════════════════════════════════════════════════════════════
อ่านบัญชี .zip จากโฟลเดอร์ input-id/ ทีละไฟล์ แล้วทำตามลำดับนี้ต่อ 1 บัญชี:

  1) restore : เปิด root → force-stop → ลบข้อมูลเดิม (clean เหมือน main.py)
               → push (คืน) ไฟล์บัญชีจาก zip กลับเข้าเครื่อง → ปิด root
  2) start   : start packet เกม (root ปิดอยู่ → เกมไม่เจอ root)
  3) event   : run_event_loops (event-back / git-item / ok-gifitem)  ← ถึงตรงนี้ = "login" แล้วหยุด
  4) export  : เปิด root → ดึงไฟล์บัญชี (เหมือนตอน backup เจอ id) → zip เก็บใน login-success/ → ปิด root
  5) ย้าย zip ต้นทางไป input-id/_done/ แล้วไปหยิบไฟล์ถัดไปจาก input-id/

engine (คลิกรูป / ADB / root toggle / event / pull) ใช้ซ้ำจาก main.py
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
import zipfile
import tempfile
import threading
import subprocess

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
    "steps": {"clean": 1, "restore": 1, "event": 1, "export": 1},
    "event_rounds": C.EVENT_LOOP_ROUNDS,
    "config_name": C.CUSTOM_CONFIG_NAME,
    "input_dir": "input-id",
    "output_dir": "login-success",
    "done_dir": "input-id/_done",
    "failed_dir": "input-id/_failed",
    "claim_dir": "input-id/_processing",
    "start_wait": 15,
    "move_done": 1,
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
                      "done_dir", "failed_dir", "claim_dir", "start_wait", "move_done"):
                if k in loaded:
                    cfg[k] = loaded[k]
            print(f"{Fore.GREEN}[CONFIG] โหลด {os.path.basename(LOGIN_CONFIG_FILE)} แล้ว{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[CONFIG] ไม่เจอ {os.path.basename(LOGIN_CONFIG_FILE)} → ใช้ค่า default{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.YELLOW}[CONFIG] อ่าน config-main.json ไม่ได้: {e} → ใช้ค่า default{Style.RESET_ALL}")

    cfg["event_rounds"] = int(cfg["event_rounds"])
    cfg["start_wait"] = int(cfg["start_wait"])
    cfg["config_name"] = str(cfg["config_name"]).strip() or C.CUSTOM_CONFIG_NAME
    cfg["move_done"] = 1 if cfg["move_done"] else 0
    LOGIN = cfg

    # push ค่าเข้า config เพื่อให้ engine เดิม (run_event_loops) ใช้ทันที
    C.EVENT_LOOP_ROUNDS = cfg["event_rounds"]
    C.CUSTOM_CONFIG_NAME = cfg["config_name"]

    enabled = [k for k, v in cfg["steps"].items() if v]
    print(f"{Fore.CYAN}[CONFIG] step ที่เปิด: {enabled} | event_rounds={cfg['event_rounds']} "
          f"| config_name='{cfg['config_name']}'{Style.RESET_ALL}")
    print(f"{Fore.CYAN}[CONFIG] input={cfg['input_dir']} → output={cfg['output_dir']} "
          f"| done={cfg['done_dir']} (move_done={cfg['move_done']}){Style.RESET_ALL}")
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
    safe = serial.replace(".", "_").replace(":", "_")
    tmp_dir = os.path.join(out_dir, f"_tmp_{safe}")
    os.makedirs(out_dir, exist_ok=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

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
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    zip_path = reserve_out_path(out_dir, out_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for local, arc in pulled:
            zf.write(local, arc)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    M.log(serial, f"✅ export → {os.path.join(os.path.basename(out_dir), os.path.basename(zip_path))}", Fore.GREEN)
    return zip_path


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
#  ทำงาน 1 บัญชี : restore → start → event → export
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

    # 3) event loops — ถึงตรงนี้ถือว่า login แล้ว → หยุด (ไม่ทำ boxes/get-item/get-pet)
    if step_on("event"):
        M.run_event_loops(device)

    M.log(serial, "ถึง login แล้ว → หยุดการทำงานฝั่งเกม", Fore.CYAN)

    # 4) export ไฟล์บัญชี (ที่อัปเดตแล้ว) ออกมาเก็บใน login-success/
    if step_on("export"):
        M.close_app(device)
        device = M.enable_root(device)
        out = export_login_zip(device, base, LOGIN["output_dir"])
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
#  วิธี claim: os.rename ย้ายไฟล์ออกจาก input-id/ → input-id/_processing/<serial>/
#  - os.rename เป็น atomic ระดับ filesystem: 2 คนย้าย source เดียวกัน มีคนเดียวสำเร็จ
#    อีกคน source หายแล้ว → OSError → ข้ามไปตัวถัดไป
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
      - ใครสร้าง <zip>.lock สำเร็จ = ได้สิทธิ์ย้ายไฟล์ (ถือ lock แค่ช่วง rename แล้วลบ)
      - lock ค้างเกิน stale_after วิ (เจ้าของ crash คา) → ยึดมาใหม่ได้
    """
    while True:
        zips = sorted(glob.glob(os.path.join(input_dir, "*.zip")))
        if not zips:
            return None
        for z in zips:
            lock = z + ".lock"
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
                dest = os.path.join(my_claim, os.path.basename(z))
                os.rename(z, dest)             # ถือ lock อยู่คนเดียว → ย้ายปลอดภัย
                return dest
            except OSError:
                continue                       # ไฟล์หายไปแล้ว → ลองตัวถัดไป
            finally:
                try:
                    os.remove(lock)            # ปลด lock (ไฟล์ถูกย้ายออกไปแล้ว)
                except OSError:
                    pass
        time.sleep(0.05)   # รอบนี้ทุกไฟล์โดน lock อยู่ชั่วขณะ → พักสั้นๆ แล้ววนใหม่


def worker(serial, input_dir):
    device = AdbClient(host="127.0.0.1", port=5037).device(serial)
    if device is None:
        M.log(serial, "ERROR: เชื่อมต่อ device ไม่ได้", Fore.RED)
        return
    my_claim = claim_dir_for(serial)

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
            with STATS_LOCK:
                STATS["done"] += 1
            if LOGIN["move_done"]:
                move_zip(zpath, LOGIN["done_dir"])     # เก็บไฟล์บัญชีเดิมไว้ที่ _done
            else:
                try:
                    os.remove(zpath)                    # ไม่เก็บ (login-success มีตัวอัปเดตแล้ว)
                except OSError:
                    pass
        else:
            with STATS_LOCK:
                STATS["fail"] += 1
            move_zip(zpath, LOGIN["failed_dir"])       # เก็บไว้ตรวจสอบ

    M.log(serial, "ไม่มีไฟล์เหลือให้ claim → จบการทำงานเครื่องนี้", Fore.GREEN)


def main():
    load_login_config()
    M.set_process_priority()

    if not M.find_adb_executable():
        print(f"{Fore.RED}[ERROR] ไม่เจอ adb.exe{Style.RESET_ALL}")
        return

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
    print(f"{Fore.GREEN}[OK] เจอ {len(devices)} device: {devices} | รอทำ ~{len(pending)} บัญชี "
          f"(แต่ละเครื่อง claim แยกกัน){Style.RESET_ALL}")

    threads = []
    for serial in devices:
        t = threading.Thread(target=worker, args=(serial, input_dir), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(2)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        M.bot_running = False
        print(f"{Fore.YELLOW}[STOP] หยุด...{Style.RESET_ALL}")

    with STATS_LOCK:
        done, fail = STATS["done"], STATS["fail"]
    print(f"{Fore.GREEN}[DONE] สำเร็จ {done} | ล้มเหลว {fail} | "
          f"ผลลัพธ์อยู่ใน {LOGIN['output_dir']}/{Style.RESET_ALL}")


if __name__ == "__main__":
    main()

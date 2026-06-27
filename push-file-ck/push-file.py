"""
push-file.py — GUI ส่ง (restore) ไฟล์บัญชี Cookie Run กลับเข้าเครื่อง
(ไฟล์เดียวจบ — รวม config + ฟังก์ชัน ADB/root จาก main.py เดิมไว้ในนี้แล้ว)

ใช้งาน:
  1) วาง .zip (จาก backup) ไว้ในโฟลเดอร์ input-id/
  2) เปิดโปรแกรม → กด "Connect ADB" (ค้างหน้าต่างไว้)
  3) ติ๊กเลือกเครื่อง → กด "Push File"
     - แตก zip → push ไฟล์กลับเข้า path เดิมอัตโนมัติ
         Cocos2dxPrefsFile.xml ฯลฯ → /data/data/com.devsisters.crg/shared_prefs
         .C80C5535...          → /data/data/com.devsisters.crg/files
     - เช็ค root ก่อน ถ้ายังไม่เปิดจะเปิดให้ (MuMu per-instance)
     - chown uid แอพ + chmod + restorecon (เกมถึงอ่านได้)
     - ส่งเสร็จ → ลบ zip ออกจาก input-id (1 zip / 1 เครื่อง)
"""
import os
import re
import sys
import glob
import json
import time
import shutil
import zipfile
import tempfile
import threading
import subprocess
import concurrent.futures

# กัน UnicodeEncodeError ตอน print ข้อความไทยลงคอนโซล (เช่น console เป็น cp1252/cp874)
# ถ้าไม่ทำ thread connect อาจตายเงียบๆ ตอน discover_devices
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:
    print("ต้องติดตั้ง customtkinter ก่อน:  pip install customtkinter")
    raise SystemExit(1)

from ppadb.client import Client as AdbClient

# colorama optional — ใช้สี log ในคอนโซลถ้ามี, ไม่มีก็รันได้ปกติ (ขาวดำ)
try:
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    class _NoColor:
        def __getattr__(self, _name):
            return ""
    Fore = Style = _NoColor()

# เปลี่ยน working directory มาที่โฟลเดอร์ของสคริปต์เสมอ (input-id/, adb/ จะชี้ถูกที่)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")

INPUT_DIR = "input-id"
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


# =========================================================
#  CONFIG  (เดิมอยู่ใน config.py — ย้ายมารวมไว้ตรงนี้)
# =========================================================
# ── Package / Path ของเกม ──
PACKAGE = "com.devsisters.crg"
DATA_DIR = "/data/data/com.devsisters.crg"

# ── เปิด/ปิด root ──
# USE_MUMU_ROOT = True  → เปิด/ปิด root ผ่าน MuMuManager (สำหรับ MuMu)
# USE_MUMU_ROOT = False → ใช้ adb root/unroot (สำหรับ AVD ธรรมดา)
USE_MUMU_ROOT = True
# path ของ MuMuManager.exe (ดูจาก info: nx_main\MuMuManager.exe)
MUMU_MANAGER = r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe"
# index ของ instance — ดูได้จาก:  MuMuManager.exe info -v all
MUMU_INDEX = "2"
# จัดการไฟล์ด้วย su -c หรือไม่ (True = MuMu, False = adb root mode)
USE_SU = True
# เวลารอหลัง adb root/unroot (เฉพาะโหมด adb root, USE_MUMU_ROOT=False) (วินาที)
ROOT_TOGGLE_WAIT = 3

# ── ไฟล์ที่ต้อง push กลับ (ต้องใช้ root) ──
SHARED_PREFS_DIR = "/data/data/com.devsisters.crg/shared_prefs"
SHARED_PREFS_FILES = [
    "Cocos2dxPrefsFile.xml",
    "com.devsisters.crg.v2.playerprefs.xml",
    "com.devsisters.plugin.appInstalledId.pref.xml",
    "com.devsisters.plugin.devsisterscontent.prefs.xml",
]
FILES_DIR = "/data/data/com.devsisters.crg/files"
FILES_FILES = [
    ".C80C5535E9E69661F4C5FCFEC98D662D",
]


# =========================================================
#  ADB / ROOT helpers  (เดิมอยู่ใน main.py — ย้ายเฉพาะที่จำเป็น)
# =========================================================
adb_path = "adb"
# map: device serial (เช่น 127.0.0.1:16448) → MuMu instance index (เช่น "2")
SERIAL_TO_INDEX = {}
MUMU_MANAGER_PATH = ""   # path ของ MuMuManager.exe ที่ resolve ได้จริง (เติมตอน connect)


def log(serial, msg, color=Fore.CYAN):
    try:
        print(f"{color}[{serial}] {msg}{Style.RESET_ALL}")
    except UnicodeEncodeError:
        print(f"[{serial}] {msg}".encode("ascii", "replace").decode("ascii"))


# ── ADB connection ──
def find_adb_executable():
    global adb_path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for loc in [os.path.join(script_dir, "adb", "adb.exe"),
                os.path.join(script_dir, "adb", "adb"),
                "adb"]:
        if os.path.exists(loc):
            try:
                r = subprocess.run([loc, "version"], capture_output=True, text=True,
                                   timeout=5, shell=(os.name == 'nt'))
                if r.returncode == 0:
                    adb_path = loc
                    print(f"{Fore.GREEN}[ADB] Verified: {adb_path}{Style.RESET_ALL}")
                    return True
            except Exception:
                pass
    found = shutil.which("adb")
    if found:
        adb_path = os.path.abspath(found)
        return True
    return False


def connect_known_ports():
    """Auto-scan emulator ports (5555-5755) แล้ว connect ทุกตัวที่ตอบ"""
    try:
        subprocess.run([adb_path, "kill-server"], capture_output=True, timeout=5, shell=(os.name == 'nt'))
        time.sleep(1)
        subprocess.run([adb_path, "start-server"], capture_output=True, timeout=5, shell=(os.name == 'nt'))
        time.sleep(1)

        ports = list(range(5555, 5756, 2))
        print(f"{Fore.YELLOW}[ADB] Auto-scanning {len(ports)} ports (5555-5755)...{Style.RESET_ALL}")

        def try_connect_port(port):
            try:
                addr = f"127.0.0.1:{port}"
                r = subprocess.run([adb_path, "connect", addr], capture_output=True,
                                   timeout=2, text=True, shell=(os.name == 'nt'))
                out = r.stdout.lower()
                if ("connected" in out or "already connected" in out) and "cannot" not in out:
                    return addr
            except Exception:
                pass
            return None

        connected = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
            futures = {ex.submit(try_connect_port, p): p for p in ports}
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res:
                    connected.append(res)
        if connected:
            print(f"{Fore.GREEN}[ADB] Found {len(connected)} device(s): {', '.join(sorted(connected))}{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}[ADB] Port scan error: {e}{Style.RESET_ALL}")


def get_connected_devices():
    try:
        r = subprocess.run([adb_path, "devices"], capture_output=True, text=True,
                           timeout=10, shell=(os.name == 'nt'))
        lines = r.stdout.strip().split("\n")[1:]
        raw = [ln.split()[0] for ln in lines if len(ln.split()) >= 2 and ln.split()[1] == "device"]
        if not raw:
            return []
        emu_ports = set()
        for d in raw:
            if d.startswith("emulator-"):
                try:
                    emu_ports.add(int(d.replace("emulator-", "")) + 1)
                except Exception:
                    pass
        final, seen = [], set()
        for d in raw:
            if d in seen:
                continue
            if d.startswith("127.0.0.1:"):
                try:
                    if int(d.split(":")[1]) in emu_ports:
                        continue
                except Exception:
                    pass
            seen.add(d)
            final.append(d)
        return final
    except Exception:
        return []


def _adb_host(serial, args, timeout=30):
    """เรียกคำสั่ง adb host-side (เช่น root/unroot/wait-for-device)"""
    cmd = [adb_path, "-s", serial] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, shell=(os.name == 'nt'))
    except Exception as e:
        log(serial, f"adb {' '.join(args)} error: {e}", Fore.YELLOW)
        return None


def _adb_connect(endpoint):
    if endpoint:
        try:
            subprocess.run([adb_path, "connect", endpoint], capture_output=True,
                           text=True, timeout=10, shell=(os.name == 'nt'))
        except Exception:
            pass


# ── MuMuManager helpers ──
def find_mumu_manager():
    """หา path ของ MuMuManager.exe — ลอง config ก่อน แล้วค่อยไล่หา/glob ตาม install ทั่วไป"""
    # 1) ใช้จาก config ถ้ามีจริง
    if MUMU_MANAGER and os.path.exists(MUMU_MANAGER):
        return MUMU_MANAGER
    # 2) candidate paths ตาม install ทั่วไป
    bases = [r"C:\Program Files\Netease", r"C:\Program Files (x86)\Netease",
             r"D:\Program Files\Netease", r"E:\Program Files\Netease"]
    subs = [r"MuMuPlayer\nx_main\MuMuManager.exe",
            r"MuMuPlayerGlobal-12.0\nx_main\MuMuManager.exe",
            r"MuMuPlayer-12.0\nx_main\MuMuManager.exe",
            r"MuMuPlayerGlobal-12.0\shell\MuMuManager.exe",
            r"MuMu Player 12\shell\MuMuManager.exe",
            r"MuMuPlayer\shell\MuMuManager.exe"]
    for b in bases:
        for s in subs:
            p = os.path.join(b, s)
            if os.path.exists(p):
                return p
    # 3) ไล่ search (glob) ในโฟลเดอร์ Netease
    for b in bases:
        if os.path.isdir(b):
            try:
                for root, _dirs, files in os.walk(b):
                    if "MuMuManager.exe" in files:
                        return os.path.join(root, "MuMuManager.exe")
            except Exception:
                pass
    return None


def _mumu(args, timeout=60):
    exe = MUMU_MANAGER_PATH or MUMU_MANAGER
    try:
        return subprocess.run([exe] + args, capture_output=True,
                              text=True, timeout=timeout, shell=(os.name == 'nt'))
    except Exception as e:
        print(f"{Fore.RED}[MuMu] error: {e}{Style.RESET_ALL}")
        return None


def get_mumu_instances():
    """
    อ่าน MuMuManager info -v all → คืน list ของ (index, serial) เฉพาะ instance ที่รันอยู่
    serial = "adb_host_ip:adb_port" (port ทางการของ MuMu จับคู่กับ index ได้แน่นอน)
    """
    r = _mumu(["info", "-v", "all"])
    if r is None:
        print(f"{Fore.RED}[MuMu] เรียก MuMuManager ไม่ได้ (เช็ค MUMU_MANAGER path){Style.RESET_ALL}")
        return []
    raw = (r.stdout or "").strip()
    if not raw:
        print(f"{Fore.RED}[MuMu] info ไม่มี output. stderr={ (r.stderr or '').strip()[:200] }{Style.RESET_ALL}")
        return []
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"{Fore.RED}[MuMu] parse info error: {e} | raw={raw[:200]}{Style.RESET_ALL}")
        return []
    # info อาจคืนเป็น dict เดียว (instance เดียว) → ห่อเป็น dict-of-dict
    if "index" in data and "adb_port" in data:
        data = {str(data.get("index", "0")): data}
    out, skipped = [], []
    for key, inf in data.items():
        if not isinstance(inf, dict):
            continue
        idx = str(inf.get("index", key))
        if inf.get("is_android_started") and inf.get("adb_port"):
            ip = inf.get("adb_host_ip", "127.0.0.1")
            out.append((idx, f"{ip}:{inf['adb_port']}"))
        else:
            skipped.append(idx)
    if skipped:
        print(f"{Fore.YELLOW}[MuMu] ข้าม instance ที่ยังไม่ start: {skipped}{Style.RESET_ALL}")
    return out


def mumu_set_root(index, on):
    """ตั้ง root_permission ของ MuMu instance ตาม index — มีผลทันที (live) ไม่ต้อง restart"""
    _mumu(["setting", "-v", str(index), "-k", "root_permission",
           "-val", "true" if on else "false"])


# ── su / shell / root toggle ──
def su_wrap(cmd):
    """wrap คำสั่ง shell ด้วย su -c ถ้าตั้ง USE_SU (ไม่งั้นรันตรงๆ เพราะ adb root แล้ว)"""
    return f"su -c '{cmd}'" if USE_SU else cmd


def _shell(device, cmd):
    try:
        return device.shell(cmd)
    except Exception as e:
        return f"__ERR__ {e}"


def is_root(device):
    """เช็คว่า adb shell เป็น root จริงไหม (uid=0) — ถ้า USE_SU เช็คผ่าน su"""
    out = _shell(device, su_wrap("id") if USE_SU else "id")
    return "uid=0" in out


def enable_root(device):
    """เปิด root ก่อนจัดการไฟล์ (MuMu: root_permission=true, live ไม่ต้อง restart)"""
    serial = device.serial
    if USE_MUMU_ROOT:
        idx = SERIAL_TO_INDEX.get(serial, MUMU_INDEX)
        log(serial, f"เปิด root (MuMu idx={idx} root_permission=true)...", Fore.YELLOW)
        mumu_set_root(idx, True)
        time.sleep(1)
    else:
        log(serial, "เปิด root (adb root)...", Fore.YELLOW)
        r = _adb_host(serial, ["root"])
        if r is not None:
            msg = (r.stdout or "").strip() or (r.stderr or "").strip()
            if msg:
                log(serial, f"  {msg}")
        _adb_host(serial, ["wait-for-device"], timeout=30)
        time.sleep(ROOT_TOGGLE_WAIT)
    if is_root(device):
        log(serial, "  ✓ root พร้อม (uid=0)", Fore.GREEN)
    else:
        log(serial, "  ✗ su ไม่ทำงาน! (เช็ค MUMU_INDEX / root ของ MuMu)", Fore.RED)
    return device


def disable_root(device):
    """ปิด root ก่อน start เกม (MuMu: root_permission=false, live ไม่ต้อง restart)
    หมายเหตุ: su ยังใช้ได้จาก adb shell แต่ตัวเกมจะไม่เจอ root"""
    serial = device.serial
    if USE_MUMU_ROOT:
        idx = SERIAL_TO_INDEX.get(serial, MUMU_INDEX)
        log(serial, f"ปิด root (MuMu idx={idx} root_permission=false) — เกมจะไม่เจอ root", Fore.YELLOW)
        mumu_set_root(idx, False)
        time.sleep(1)
    else:
        log(serial, "ปิด root (adb unroot)...", Fore.YELLOW)
        r = _adb_host(serial, ["unroot"])
        if r is not None:
            msg = (r.stdout or "").strip() or (r.stderr or "").strip()
            if msg:
                log(serial, f"  {msg}")
        _adb_host(serial, ["wait-for-device"], timeout=30)
        time.sleep(ROOT_TOGGLE_WAIT)
        if is_root(device):
            log(serial, "  ✗ ยังเป็น root อยู่", Fore.RED)
        else:
            log(serial, "  ✓ ปิด root แล้ว (uid≠0)", Fore.GREEN)
    return device


def _device_online(serial):
    r = _adb_host(serial, ["get-state"], timeout=8)
    return r is not None and r.stdout.strip() == "device"


def discover_devices():
    """
    คืน list ของ serial ที่จะใช้
    - โหมด MuMu: อ่าน instance จาก MuMuManager แล้ว connect ผ่าน adb_port ทางการ
      (ได้ serial ที่ map กับ index แน่นอน → toggle root ถูกตัว) แต่ละเครื่อง
    - ถ้า MuMu ไม่ให้ข้อมูล → fallback เป็น port-scan ปกติ
    """
    global MUMU_MANAGER_PATH
    if USE_MUMU_ROOT:
        MUMU_MANAGER_PATH = find_mumu_manager() or ""
        if MUMU_MANAGER_PATH:
            print(f"{Fore.GREEN}[MuMu] ใช้ MuMuManager: {MUMU_MANAGER_PATH}{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}[MuMu] หา MuMuManager.exe ไม่เจอ! แก้ MUMU_MANAGER ในไฟล์นี้{Style.RESET_ALL}")

        instances = get_mumu_instances()
        if instances:
            devices = []
            for idx, serial in instances:
                _adb_connect(serial)
                ok = _device_online(serial)
                SERIAL_TO_INDEX[serial] = idx
                devices.append(serial)
                tag = "✓" if ok else "✗ offline"
                print(f"{Fore.GREEN}[MuMu] instance idx={idx} → {serial} {tag}{Style.RESET_ALL}")
            time.sleep(1)
            if devices:
                return devices
        print(f"{Fore.YELLOW}[MuMu] อ่าน instance ไม่ได้ → ใช้ port-scan แทน (root อาจ map index ไม่ตรง){Style.RESET_ALL}")

    connect_known_ports()
    return get_connected_devices()


# =========================================================
#  Push logic (เหมือน CLI แต่ส่ง log ออกทาง callback)
# =========================================================
def adb_push(serial, local, remote):
    try:
        subprocess.run([adb_path, "-s", serial, "push", local, remote],
                       capture_output=True, text=True, timeout=120, **NO_WINDOW)
        return True
    except Exception:
        return False


def get_app_uid(device):
    """หา uid ของแอพ (ไว้ chown ไฟล์ที่ push กลับ)"""
    out = _shell(device, su_wrap(f"stat -c %u {DATA_DIR}")).strip()
    digits = "".join(ch for ch in out if ch.isdigit())
    if digits:
        return digits
    out2 = _shell(device, f"dumpsys package {PACKAGE}")
    m = re.search(r"userId=(\d+)", out2)
    return m.group(1) if m else None


def target_for(fname):
    if fname in SHARED_PREFS_FILES:
        return SHARED_PREFS_DIR, "660"
    if fname in FILES_FILES:
        return FILES_DIR, "600"
    return None, None


def push_into(device, serial, local, fname, dest_dir, mode, uid):
    rtmp = f"/sdcard/{fname}"
    if not adb_push(serial, local, rtmp):
        return False
    device.shell(su_wrap(f"mkdir -p {dest_dir}"))
    device.shell(su_wrap(f"chown {uid}:{uid} {dest_dir}"))
    device.shell(su_wrap(f"cp {rtmp} {dest_dir}/{fname}"))
    device.shell(su_wrap(f"chown {uid}:{uid} {dest_dir}/{fname}"))
    device.shell(su_wrap(f"chmod {mode} {dest_dir}/{fname}"))
    device.shell(su_wrap(f"restorecon {dest_dir}/{fname}"))   # best-effort fix SELinux
    device.shell(f"rm -f {rtmp}")
    out = _shell(device, su_wrap(f"[ -e {dest_dir}/{fname} ] && echo OK || echo NO")).strip()
    return "OK" in out


def push_one(device, serial, zpath, logfn):
    name = os.path.basename(zpath)
    logfn(f"[{serial}] === PUSH {name} ===")

    device.shell(f"am force-stop {PACKAGE}")
    time.sleep(1)

    if is_root(device):
        logfn(f"[{serial}] root เปิดอยู่แล้ว ✓")
    else:
        logfn(f"[{serial}] root ยังไม่เปิด → กำลังเปิด...")
        device = enable_root(device)
    if not is_root(device):
        logfn(f"[{serial}] ✗ เปิด root ไม่ได้ → ยกเลิก")
        return False

    uid = get_app_uid(device)
    if not uid:
        logfn(f"[{serial}] ✗ หา uid ของแอพไม่ได้ → ยกเลิก")
        return False
    logfn(f"[{serial}] app uid = {uid}")

    tmp = os.path.join(tempfile.gettempdir(), "cr_push_" + serial.replace(".", "_").replace(":", "_"))
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)
    except Exception as e:
        logfn(f"[{serial}] ✗ แตก zip ไม่ได้: {e}")
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
            logfn(f"[{serial}]   ข้าม (ไม่รู้ปลายทาง): {fname}")
            continue
        if push_into(device, serial, local, fname, dest_dir, mode, uid):
            logfn(f"[{serial}]   ส่งแล้ว ✓ {fname}")
            pushed += 1
        else:
            logfn(f"[{serial}]   ส่งไม่ได้ ✗ {fname}")
            all_ok = False
    shutil.rmtree(tmp, ignore_errors=True)

    disable_root(device)   # ปิด root ให้พร้อมเปิดเกม
    return all_ok and pushed > 0


# =========================================================
#  GUI
# =========================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("🍪 Cookie Run - Push File")
        self.geometry("540x700")
        self.devices = []
        self.device_vars = {}
        self.status_labels = {}
        self.setup_ui()
        if not find_adb_executable():
            messagebox.showerror("Error", "❌ ไม่เจอ adb.exe")
        else:
            # เชื่อม ADB อัตโนมัติตั้งแต่เปิดโปรแกรม (ค้างหน้าต่างไว้รอกด Push)
            self.after(500, self.on_connect)

    def setup_ui(self):
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10, padx=20, fill="x")

        self.btn_connect = ctk.CTkButton(btn_frame, text="🔌 Connect ADB",
                                         font=ctk.CTkFont(size=13, weight="bold"),
                                         fg_color="#E5C07B", text_color="#21252B",
                                         hover_color="#D1B071", command=self.on_connect, height=40)
        self.btn_connect.pack(side="left", expand=True, padx=3, fill="x")

        self.btn_push = ctk.CTkButton(btn_frame, text="🚀 Push File",
                                      font=ctk.CTkFont(size=13, weight="bold"),
                                      fg_color="#61AFEF", text_color="#21252B",
                                      hover_color="#5294CB", command=self.on_push, height=40)
        self.btn_push.pack(side="left", expand=True, padx=3, fill="x")

        self.lbl_status = ctk.CTkLabel(self, text="Waiting for connection...", font=ctk.CTkFont(size=12))
        self.lbl_status.pack(pady=(0, 5))

        ctk.CTkLabel(self, text="☑ Select Devices:", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(5, 3), padx=20, anchor="w")
        self.dev_scroll = ctk.CTkScrollableFrame(self, fg_color="#2b2b2b", corner_radius=8, height=200)
        self.dev_scroll.pack(fill="both", expand=True, padx=20, pady=(0, 5))

        ctk.CTkLabel(self, text="📋 Log:", font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(5, 2), padx=20, anchor="w")
        self.log_text = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=10),
                                       fg_color="#1e1e1e", text_color="#8b949e", height=160)
        self.log_text.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        self.log_text.configure(state="disabled")

    # ── thread-safe helpers ──
    def log_message(self, msg):
        self.after(0, self._log, msg)

    def _log(self, msg):
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def set_status(self, dev, text, color):
        self.after(0, lambda: self._set_status(dev, text, color))

    def _set_status(self, dev, text, color):
        if dev in self.status_labels:
            try:
                self.status_labels[dev].configure(text=text, text_color=color)
            except Exception:
                pass

    def set_buttons_state(self, state):
        self.after(0, lambda: (self.btn_connect.configure(state=state),
                               self.btn_push.configure(state=state)))

    # ── Connect ──
    def on_connect(self):
        self.set_buttons_state("disabled")
        self.lbl_status.configure(text="⏳ Searching for devices...", text_color="#E5C07B")
        threading.Thread(target=self.connect_task, daemon=True).start()

    def connect_task(self):
        devices = discover_devices()
        self.devices = devices
        self.after(0, self._render_devices, devices)
        self.set_buttons_state("normal")

    def _render_devices(self, devices):
        for w in self.dev_scroll.winfo_children():
            w.destroy()
        self.device_vars.clear()
        self.status_labels.clear()
        if not devices:
            self.lbl_status.configure(text="🔴 No devices found!", text_color="#E06C75")
            return
        self.lbl_status.configure(text=f"🟢 Found {len(devices)} devices", text_color="#98C379")
        for i, dev in enumerate(devices):
            idx = SERIAL_TO_INDEX.get(dev, "?")
            frame = ctk.CTkFrame(self.dev_scroll, fg_color="#383838", corner_radius=6, height=36)
            frame.pack(fill="x", pady=2, padx=2)
            frame.pack_propagate(False)
            var = ctk.BooleanVar(value=True)
            self.device_vars[dev] = var
            ctk.CTkCheckBox(frame, text="", variable=var, width=20, height=20,
                            checkbox_width=18, checkbox_height=18).pack(side="left", padx=(10, 5), pady=8)
            ctk.CTkLabel(frame, text=f"#{i+1} (idx={idx})", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=5)
            ctk.CTkLabel(frame, text=dev, font=ctk.CTkFont(family="Consolas", size=11), text_color="#ccc").pack(side="left", padx=5)
            lbl = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=11, weight="bold"))
            lbl.pack(side="right", padx=10)
            self.status_labels[dev] = lbl

    # ── Push ──
    def on_push(self):
        targets = [d for d, v in self.device_vars.items() if v.get()]
        if not targets:
            messagebox.showwarning("Warning", "ยังไม่ได้เลือกเครื่อง!")
            return
        os.makedirs(INPUT_DIR, exist_ok=True)
        zips = sorted(glob.glob(os.path.join(INPUT_DIR, "*.zip")))
        if not zips:
            messagebox.showerror("Error", f"ไม่มี .zip ใน {INPUT_DIR}/")
            return
        for d in targets:
            self.set_status(d, "⏳ Running...", "#E5C07B")
        self.set_buttons_state("disabled")
        threading.Thread(target=self.push_manager, args=(targets, zips), daemon=True).start()

    def push_manager(self, targets, zips):
        q = list(zips)
        qlock = threading.Lock()
        results = {"done": 0, "fail": 0}   # สรุปผลไว้โชว์ในป๊อปอัปตอนจบ
        self.log_message(f"[INFO] {len(targets)} เครื่อง | {len(zips)} zip (ส่ง 1 zip/เครื่อง)")

        def worker(serial):
            try:
                device = AdbClient(host="127.0.0.1", port=5037).device(serial)
                if device is None:
                    self.set_status(serial, "❌ no dev", "#E06C75")
                    return
                with qlock:
                    if not q:
                        self.set_status(serial, "— no zip", "#888888")
                        return
                    zpath = q.pop(0)
                if push_one(device, serial, zpath, self.log_message):
                    try:
                        os.remove(zpath)
                    except Exception:
                        pass
                    self.log_message(f"[{serial}] ✅ ส่งเสร็จ + ลบ {os.path.basename(zpath)} ออกจาก input-id")
                    self.set_status(serial, "✅ DONE", "#98C379")
                    with qlock:
                        results["done"] += 1
                else:
                    self.log_message(f"[{serial}] ❌ ส่งไม่ครบ — เก็บ {os.path.basename(zpath)} ไว้")
                    self.set_status(serial, "❌ FAIL", "#E06C75")
                    with qlock:
                        results["fail"] += 1
            except Exception as e:
                self.log_message(f"[{serial}] [ERROR] {e}")
                self.set_status(serial, "❌ ERROR", "#E06C75")
                with qlock:
                    results["fail"] += 1

        ts = [threading.Thread(target=worker, args=(s,), daemon=True) for s in targets]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        with qlock:
            rem = len(q)
        self.log_message(f"[DONE] เหลือ zip ใน {INPUT_DIR}: {rem}")
        self.set_buttons_state("normal")
        self.after(0, lambda: self.show_done_popup(results["done"], results["fail"], rem))

    # ── ป๊อปอัปสรุปผล (เด้งกลางจอ) ──
    def show_done_popup(self, done, fail, rem):
        if fail == 0 and done > 0:
            title, icon, accent = "ส่งเสร็จแล้ว", "✅", "#98C379"
            head = "ส่งไฟล์เสร็จแล้ว!"
        elif done == 0 and fail == 0:
            title, icon, accent = "ไม่มีอะไรถูกส่ง", "ℹ️", "#E5C07B"
            head = "ไม่มีไฟล์ถูกส่ง"
        else:
            title, icon, accent = "ส่งไม่ครบ", "⚠️", "#E06C75"
            head = "ส่งเสร็จ แต่มีบางเครื่องไม่สำเร็จ"

        detail = f"สำเร็จ {done} เครื่อง"
        if fail:
            detail += f"   |   ไม่สำเร็จ {fail} เครื่อง"
        detail += f"\nเหลือ zip ใน {INPUT_DIR}: {rem}"

        popup = ctk.CTkToplevel(self)
        popup.title(title)
        w, h = 380, 210
        sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        popup.geometry(f"{w}x{h}+{x}+{y}")     # เด้งกลางจอ
        popup.resizable(False, False)
        popup.configure(fg_color="#21252B")

        ctk.CTkLabel(popup, text=icon, font=ctk.CTkFont(size=48)).pack(pady=(22, 4))
        ctk.CTkLabel(popup, text=head, font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=accent).pack(pady=(0, 4))
        ctk.CTkLabel(popup, text=detail, font=ctk.CTkFont(size=12),
                     text_color="#ccc", justify="center").pack(pady=(0, 12))
        ctk.CTkButton(popup, text="OK", width=120, height=36,
                      fg_color=accent, text_color="#21252B",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=popup.destroy).pack()

        popup.transient(self)
        popup.attributes("-topmost", True)
        popup.after(250, popup.grab_set)       # ทำเป็น modal (รอ CTkToplevel พร้อมก่อน)
        popup.after(300, popup.focus_force)


if __name__ == "__main__":
    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass

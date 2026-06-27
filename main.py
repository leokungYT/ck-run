import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"

import cv2
import numpy as np
import time
import subprocess
import threading
import shutil
import zipfile
import json
import re
import concurrent.futures
from ppadb.client import Client as AdbClient
from colorama import Fore, Style, init

# เปลี่ยน working directory มาที่โฟลเดอร์ของสคริปต์เสมอ (relative path เช่น 'img' จะชี้ถูกที่)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

cv2.setNumThreads(1)
init(autoreset=True)

import config as C

# ── Global ───────────────────────────────────────────────────────────
adb_path = "adb"
bot_running = False

SCREENCAP_SCALE = 1.0           # ไม่ย่อภาพ → coordinate ตรงกับ template เดิม
IMAGE_CACHE = {}
_image_cache_lock = threading.Lock()

# map: device serial (เช่น 127.0.0.1:16448) → MuMu instance index (เช่น "2")
SERIAL_TO_INDEX = {}
MUMU_MANAGER_PATH = ""   # path ของ MuMuManager.exe ที่ resolve ได้จริง (เติมตอน start)

os.makedirs(C.BACKUP_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  Runtime config (configmain.json)  — แก้ผ่าน GUI ได้ (gui.py)
#  main.py = engine จริง | configmain.json = เปิด/ปิดแต่ละ step
# ═══════════════════════════════════════════════════════════════════
CONFIGMAIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configmain.json")
RUNTIME = {
    "steps": dict(C.STEPS),
    "event_rounds": C.EVENT_LOOP_ROUNDS,
    "config_name": C.CUSTOM_CONFIG_NAME,
    "split_by_count": C.SPLIT_BACKUP_BY_COUNT,
}

# สถิติ (ให้ GUI อ่านไปแสดง)
STATS = {"cycles": 0, "backups": 0, "no_match": 0, "found": {}}
STATS_LOCK = threading.Lock()


def load_runtime_config():
    """โหลด configmain.json มาทับค่า default (ถ้าไม่มีไฟล์ → ใช้ค่าใน config.py)"""
    global RUNTIME
    steps = dict(C.STEPS)
    event_rounds = C.EVENT_LOOP_ROUNDS
    config_name = C.CUSTOM_CONFIG_NAME
    split_by_count = C.SPLIT_BACKUP_BY_COUNT
    try:
        if os.path.exists(CONFIGMAIN_FILE):
            with open(CONFIGMAIN_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in (loaded.get("steps") or {}).items():
                key = k.replace("-", "_")   # รองรับทั้ง check-ruby และ check_ruby
                if key in steps:
                    steps[key] = 1 if v else 0
            event_rounds = int(loaded.get("event_rounds", event_rounds))
            config_name = str(loaded.get("config_name", config_name)).strip() or config_name
            split_by_count = 1 if loaded.get("split_by_count", split_by_count) else 0
            print(f"{Fore.GREEN}[CONFIG] โหลด {os.path.basename(CONFIGMAIN_FILE)} แล้ว{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.YELLOW}[CONFIG] อ่าน configmain.json ไม่ได้: {e} → ใช้ค่า default{Style.RESET_ALL}")
    RUNTIME = {"steps": steps, "event_rounds": event_rounds, "config_name": config_name,
               "split_by_count": split_by_count}
    # push ค่าเข้า C เพื่อให้โค้ดเดิม (run_play_sequence / run_event_loops) ใช้ได้ทันที
    C.EVENT_LOOP_ROUNDS = event_rounds
    C.CUSTOM_CONFIG_NAME = config_name
    C.SPLIT_BACKUP_BY_COUNT = split_by_count
    enabled = [k for k, v in steps.items() if v]
    print(f"{Fore.CYAN}[CONFIG] step ที่เปิด: {enabled} | event_rounds={event_rounds} | config_name='{config_name}'{Style.RESET_ALL}")
    return RUNTIME


def save_runtime_config(steps=None, event_rounds=None, config_name=None, split_by_count=None):
    """เซฟ configmain.json (เรียกจาก GUI) แล้วคืน dict ที่เซฟ"""
    data = {
        "steps": steps if steps is not None else RUNTIME["steps"],
        "event_rounds": event_rounds if event_rounds is not None else RUNTIME["event_rounds"],
        "config_name": config_name if config_name is not None else RUNTIME["config_name"],
        "split_by_count": split_by_count if split_by_count is not None else RUNTIME.get("split_by_count", 0),
    }
    with open(CONFIGMAIN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def step_on(name):
    """ขั้นตอน name เปิดอยู่ไหม (อ่านจาก RUNTIME ที่ load_runtime_config ตั้งไว้)"""
    return bool(RUNTIME.get("steps", {}).get(name, 1))


_priority_set = False


def set_process_priority():
    """ลด priority ของ process ลง (BELOW_NORMAL) กันบอทแย่ง CPU จน UI/Explorer ค้าง.
    เรียกครั้งเดียวพอ (idempotent)"""
    global _priority_set
    if _priority_set or not getattr(C, "LOW_PRIORITY", 1):
        return
    _priority_set = True

    # 1) psutil (ชัวร์สุด ข้ามเรื่อง signature)
    try:
        import psutil
        p = psutil.Process()
        if os.name == "nt":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
        print(f"{Fore.CYAN}[PERF] process priority = BELOW_NORMAL (กัน UI/Explorer ค้าง){Style.RESET_ALL}")
        return
    except Exception:
        pass

    # 2) fallback: ctypes (ตั้ง signature ให้ถูกสำหรับ 64-bit)
    try:
        if os.name == "nt":
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            if k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
                print(f"{Fore.CYAN}[PERF] process priority = BELOW_NORMAL (กัน UI/Explorer ค้าง){Style.RESET_ALL}")
        else:
            os.nice(10)
    except Exception as e:
        print(f"{Fore.YELLOW}[PERF] set priority error: {e}{Style.RESET_ALL}")


def log(serial, msg, color=Fore.CYAN):
    try:
        print(f"{color}[{serial}] {msg}{Style.RESET_ALL}")
    except UnicodeEncodeError:
        print(f"[{serial}] {msg}".encode("ascii", "replace").decode("ascii"))


# ═══════════════════════════════════════════════════════════════════
#  ADB connection  (วิธีเดียวกับ pes/main-pes.py)
# ═══════════════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════════════
#  Screencap + Image search  (วิธีเดียวกับ pes/main-pes.py)
# ═══════════════════════════════════════════════════════════════════
_MIN_SCREENCAP_INTERVAL = 0.25
_LAST_SCREENCAP_TS = {}


def fast_screencap(device):
    """Screencap จาก raw RGBA → gray (เร็วกว่า PNG)"""
    serial = device.serial
    wait = _MIN_SCREENCAP_INTERVAL - (time.time() - _LAST_SCREENCAP_TS.get(serial, 0.0))
    if wait > 0:
        time.sleep(wait)
    _LAST_SCREENCAP_TS[serial] = time.time()

    conn = None
    try:
        conn = device.client.create_connection(timeout=device.client.timeout)
        conn.send(f"host:transport:{device.serial}")
        conn.check_status()
        conn.send("shell:screencap")
        conn.check_status()
        raw = conn.read_all()
        if len(raw) > 16:
            w = int.from_bytes(raw[0:4], 'little')
            h = int.from_bytes(raw[4:8], 'little')
            expected = w * h * 4
            if len(raw) >= 12 + expected:
                gray = cv2.cvtColor(
                    np.frombuffer(raw, dtype=np.uint8, offset=12, count=expected).reshape((h, w, 4)),
                    cv2.COLOR_RGBA2GRAY)
                if SCREENCAP_SCALE != 1.0:
                    gray = cv2.resize(gray, (int(w * SCREENCAP_SCALE), int(h * SCREENCAP_SCALE)),
                                      interpolation=cv2.INTER_LINEAR)
                return gray
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    # Fallback PNG
    try:
        raw = device.screencap()
        if raw:
            gray = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is not None and SCREENCAP_SCALE != 1.0:
                gray = cv2.resize(gray, None, fx=SCREENCAP_SCALE, fy=SCREENCAP_SCALE,
                                  interpolation=cv2.INTER_LINEAR)
            return gray
    except Exception:
        pass
    return None


def load_template(path):
    with _image_cache_lock:
        if path in IMAGE_CACHE:
            return IMAGE_CACHE[path]
    t = None
    base, ext = os.path.splitext(path)
    alt = base + (".png" if ext.lower() == ".bmp" else ".bmp")
    for cand in [path, alt]:
        if os.path.exists(cand):
            t = cv2.imread(cand, cv2.IMREAD_GRAYSCALE)
            if t is not None:
                break
    if t is not None and SCREENCAP_SCALE != 1.0:
        t = cv2.resize(t, (max(1, int(t.shape[1] * SCREENCAP_SCALE)),
                           max(1, int(t.shape[0] * SCREENCAP_SCALE))),
                       interpolation=cv2.INTER_LINEAR)
    with _image_cache_lock:
        IMAGE_CACHE[path] = t
    return t


def _match_template(img_gray, path, threshold):
    tpl = load_template(path)
    if tpl is None:
        return []
    nh, nw = tpl.shape[0], tpl.shape[1]
    result = cv2.matchTemplate(img_gray, tpl, cv2.TM_CCOEFF_NORMED)
    locs = list(zip(*np.where(result >= threshold)[::-1]))
    if not locs:
        return []
    rects = []
    for loc in locs:
        rect = [int(loc[0]), int(loc[1]), nw, nh]
        rects.append(rect)
        rects.append(rect)
    rects, _ = cv2.groupRectangles(rects, groupThreshold=1, eps=1)
    inv = 1.0 / SCREENCAP_SCALE if SCREENCAP_SCALE != 1.0 else 1.0
    return [(int((x + w / 2) * inv), int((y + h / 2) * inv)) for (x, y, w, h) in rects]


def ImgSearchADB(img, path, threshold=C.MATCH_THRESHOLD):
    try:
        if img is None:
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        pts = _match_template(gray, path, threshold)
        if not pts:
            base, ext = os.path.splitext(path)
            alt = base + (".png" if ext.lower() == ".bmp" else ".bmp")
            if os.path.exists(alt):
                pts = _match_template(gray, alt, threshold)
        return pts
    except Exception as e:
        print(f"Error in ImgSearchADB: {e}")
        return []


# ── helper คลิก ──────────────────────────────────────────────────────
def tap(device, x, y):
    device.shell(f"input swipe {x} {y} {x} {y} 100")


def img_path(name, folder=C.IMG_DIR):
    return os.path.join(folder, name)


# ═══════════════════════════════════════════════════════════════════
#  Generic flow helpers
# ═══════════════════════════════════════════════════════════════════
def wait_and_click(device, name, timeout=C.DEFAULT_WAIT, required=True,
                   post_delay=1.0, folder=C.IMG_DIR, threshold=C.MATCH_THRESHOLD):
    """รอ template โผล่แล้วคลิก. คืน True ถ้าคลิกแล้ว, False ถ้า timeout"""
    path = img_path(name, folder)
    start = time.time()
    while time.time() - start < timeout:
        if not bot_running:
            return False
        img = fast_screencap(device)
        pts = ImgSearchADB(img, path, threshold)
        if pts:
            x, y = pts[0]
            log(device.serial, f"คลิก {name} ที่ ({x},{y})")
            tap(device, x, y)
            time.sleep(post_delay)
            return True
        time.sleep(0.3)
    if required:
        log(device.serial, f"⏰ timeout: ไม่เจอ {name} ใน {timeout}s", Fore.YELLOW)
    return False


def click_fixed(device, x, y, label="", post_delay=1.0):
    log(device.serial, f"คลิกตำแหน่ง ({x},{y}) {label}")
    tap(device, x, y)
    time.sleep(post_delay)


def handle_repeating(device, name, appear_timeout=C.APPEAR_TIMEOUT, absent_secs=C.ABSENT_SECS):
    """
    รูปแบบ event-back / git-item / ok-gifitem:
    - รอรูปโผล่ครั้งแรกไม่เกิน appear_timeout (ถ้าไม่โผล่เลย → ข้าม)
    - จากนั้นกดรัวๆ ทุกครั้งที่เจอ จนกว่าจะ "ไม่เจอ" ติดต่อกันครบ absent_secs วิ
    """
    path = img_path(name)
    log(device.serial, f"--- handle {name} (appear<{appear_timeout}s, absent {absent_secs}s) ---", Fore.MAGENTA)

    # 1) รอโผล่ครั้งแรก
    start = time.time()
    seen = False
    while time.time() - start < appear_timeout:
        if not bot_running:
            return
        img = fast_screencap(device)
        pts = ImgSearchADB(img, path)
        if pts:
            x, y = pts[0]
            tap(device, x, y)
            log(device.serial, f"เจอ {name} ครั้งแรก คลิก ({x},{y})")
            seen = True
            break
        time.sleep(0.3)
    if not seen:
        log(device.serial, f"ไม่เจอ {name} ใน {appear_timeout}s — ข้าม", Fore.YELLOW)
        return

    # 2) กดรัวๆ จนหายครบ absent_secs
    last_seen = time.time()
    while time.time() - last_seen < absent_secs:
        if not bot_running:
            return
        img = fast_screencap(device)
        pts = ImgSearchADB(img, path)
        if pts:
            x, y = pts[0]
            tap(device, x, y)
            last_seen = time.time()
        time.sleep(0.4)
    log(device.serial, f"{name} หายครบ {absent_secs}s → ไปต่อ")


# ═══════════════════════════════════════════════════════════════════
#  Root toggle / game start / file ops
# ═══════════════════════════════════════════════════════════════════
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


# ── MuMuManager helpers ──────────────────────────────────────────────
def find_mumu_manager():
    """หา path ของ MuMuManager.exe — ลอง config ก่อน แล้วค่อยไล่หา/glob ตาม install ทั่วไป"""
    # 1) ใช้จาก config ถ้ามีจริง
    if C.MUMU_MANAGER and os.path.exists(C.MUMU_MANAGER):
        return C.MUMU_MANAGER
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
    exe = MUMU_MANAGER_PATH or C.MUMU_MANAGER
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


def su_wrap(cmd):
    """wrap คำสั่ง shell ด้วย su -c ถ้าตั้ง USE_SU (ไม่งั้นรันตรงๆ เพราะ adb root แล้ว)"""
    return f"su -c '{cmd}'" if C.USE_SU else cmd


def _shell(device, cmd):
    try:
        return device.shell(cmd)
    except Exception as e:
        return f"__ERR__ {e}"


def is_root(device):
    """เช็คว่า adb shell เป็น root จริงไหม (uid=0) — ถ้า USE_SU เช็คผ่าน su"""
    out = _shell(device, su_wrap("id") if C.USE_SU else "id")
    return "uid=0" in out


def enable_root(device):
    """เปิด root ก่อนจัดการไฟล์ (MuMu: root_permission=true, live ไม่ต้อง restart)"""
    serial = device.serial
    if C.USE_MUMU_ROOT:
        idx = SERIAL_TO_INDEX.get(serial, C.MUMU_INDEX)
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
        time.sleep(C.ROOT_TOGGLE_WAIT)
    if is_root(device):
        log(serial, "  ✓ root พร้อม (uid=0)", Fore.GREEN)
    else:
        log(serial, "  ✗ su ไม่ทำงาน! (เช็ค MUMU_INDEX / root ของ MuMu)", Fore.RED)
    return device


def disable_root(device):
    """ปิด root ก่อน start เกม (MuMu: root_permission=false, live ไม่ต้อง restart)
    หมายเหตุ: su ยังใช้ได้จาก adb shell แต่ตัวเกมจะไม่เจอ root"""
    serial = device.serial
    if C.USE_MUMU_ROOT:
        idx = SERIAL_TO_INDEX.get(serial, C.MUMU_INDEX)
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
        time.sleep(C.ROOT_TOGGLE_WAIT)
        if is_root(device):
            log(serial, "  ✗ ยังเป็น root อยู่", Fore.RED)
        else:
            log(serial, "  ✓ ปิด root แล้ว (uid≠0)", Fore.GREEN)
    return device


def start_game(device):
    """start packet เกม cookie run"""
    log(device.serial, f"force-stop + start {C.PACKAGE}", Fore.GREEN)
    device.shell(f"am force-stop {C.PACKAGE}")
    time.sleep(2)
    device.shell(f"monkey -p {C.PACKAGE} -c android.intent.category.LAUNCHER 1")


def close_app(device):
    """ปิดเกม (force-stop) — ไม่ใช้ pm clear เพื่อกัน asset เกมโดนลบแล้วต้องโหลดใหม่
    การ reset บัญชีทำผ่าน delete_account_files() แทน"""
    log(device.serial, f"ปิดเกม {C.PACKAGE}", Fore.YELLOW)
    device.shell(f"am force-stop {C.PACKAGE}")
    time.sleep(2)


def delete_account_files(device):
    """ลบไฟล์ save (ต้องเปิด root ก่อน) + verify ว่าหายจริง"""
    serial = device.serial
    log(serial, "ลบไฟล์ save...", Fore.YELLOW)
    targets = ([f"{C.SHARED_PREFS_DIR}/{f}" for f in C.SHARED_PREFS_FILES]
               + [f"{C.FILES_DIR}/{f}" for f in C.FILES_FILES])
    for path in targets:
        device.shell(su_wrap(f"rm -f {path}"))
    time.sleep(0.5)
    # verify ทีละไฟล์ว่าหายจริงไหม
    for path in targets:
        out = _shell(device, su_wrap(f"[ -e {path} ] && echo EXIST || echo GONE")).strip()
        name = path.split("/")[-1]
        if "GONE" in out:
            log(serial, f"  ลบแล้ว ✓ {name}", Fore.GREEN)
        else:
            log(serial, f"  ลบไม่ได้ ✗ {name}  ({out})", Fore.RED)
    time.sleep(0.5)


def pull_file(serial, remote, local):
    """ดึงไฟล์ binary จากเครื่อง (exec-out กัน CRLF เพี้ยน; adb root แล้วอ่านได้เลย)"""
    try:
        if C.USE_SU:
            cmd = [adb_path, "-s", serial, "exec-out", "su", "-c", f"cat '{remote}'"]
        else:
            cmd = [adb_path, "-s", serial, "exec-out", "cat", remote]
        with open(local, "wb") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, shell=(os.name == 'nt'))
        if r.returncode == 0 and os.path.exists(local) and os.path.getsize(local) > 0:
            return True
        if os.path.exists(local):
            os.remove(local)
        return False
    except Exception:
        return False


_backup_name_lock = threading.Lock()


def extract_member_id(xml_path):
    """ดึงค่า member_id จาก Cocos2dxPrefsFile.xml
    เช่น <string name="member_id">DPDDH7496</string> → 'DPDDH7496'"""
    try:
        with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        m = re.search(rf'name="{re.escape(C.MEMBER_ID_KEY)}"\s*>([^<]*)</string>', txt)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None


def reserve_zip_path(zip_name, subdir=None):
    """หาชื่อ zip ที่ว่าง: <name>.zip, <name>_2.zip, <name>_3.zip ...
    แล้วจองชื่อทันที (สร้างไฟล์เปล่า) แบบ atomic กันหลาย thread ได้ชื่อชนกัน
    subdir = โฟลเดอร์ย่อยใน backup/ (เช่น 'find-2') ถ้า None = เก็บใน backup/ ตรงๆ"""
    with _backup_name_lock:
        out_dir = os.path.join(C.BACKUP_DIR, subdir) if subdir else C.BACKUP_DIR
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{zip_name}.zip")
        i = 2
        while os.path.exists(path):
            path = os.path.join(out_dir, f"{zip_name}_{i}.zip")
            i += 1
        open(path, "wb").close()   # จองชื่อไว้ก่อน
        return path


def export_backup_zip(device, zip_name, ruby=None, subdir=None):
    """ดึง shared_prefs + files ทั้งหมด แล้ว zip เก็บไว้ใน backup/<subdir>/<zip_name>.zip
    ถ้ามี ruby (เลขจาก check-ruby) จะต่อท้ายเป็น [ruby] เช่น name+[ID]+[315].zip
    subdir = โฟลเดอร์ย่อย (เช่น 'find-2') ถ้า None = เก็บใน backup/ ตรงๆ"""
    serial = device.serial
    safe = serial.replace(".", "_").replace(":", "_")
    tmp_dir = os.path.join(C.BACKUP_DIR, f"_tmp_{safe}")
    os.makedirs(tmp_dir, exist_ok=True)

    pulled = []
    for f in C.SHARED_PREFS_FILES:
        local = os.path.join(tmp_dir, f)
        if pull_file(serial, f"{C.SHARED_PREFS_DIR}/{f}", local):
            pulled.append((local, f))
            log(serial, f"  pulled prefs: {f}", Fore.GREEN)
        else:
            log(serial, f"  ⚠️ pull ล้มเหลว: {f}", Fore.YELLOW)
    for f in C.FILES_FILES:
        local = os.path.join(tmp_dir, f)
        if pull_file(serial, f"{C.FILES_DIR}/{f}", local):
            pulled.append((local, f))
            log(serial, f"  pulled files: {f}", Fore.GREEN)
        else:
            log(serial, f"  ⚠️ pull ล้มเหลว: {f}", Fore.YELLOW)

    if not pulled:
        log(serial, "ไม่มีไฟล์ให้ zip", Fore.RED)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    # ── ดึง member_id จาก Cocos2dxPrefsFile.xml มาต่อท้ายชื่อ → name+[ID]+ ──
    mid_local = os.path.join(tmp_dir, C.MEMBER_ID_FILE)
    member_id = extract_member_id(mid_local) if os.path.exists(mid_local) else None
    if member_id:
        zip_name = f"{zip_name}+[{member_id}]+"
        log(serial, f"  member_id = {member_id}", Fore.GREEN)
    else:
        log(serial, "  ⚠️ อ่าน member_id ไม่ได้", Fore.YELLOW)

    # ── ต่อท้ายเลข ruby (จาก check-ruby) → name+[ID]+[315] ──
    if ruby:
        sep = "" if zip_name.endswith("+") else "+"
        zip_name = f"{zip_name}{sep}[{ruby}]"
        log(serial, f"  ruby = {ruby}", Fore.GREEN)

    # ตั้งชื่อสะอาด: <name>.zip, <name>_2.zip ... (atomic กัน thread ชนกัน)
    zip_path = reserve_zip_path(zip_name, subdir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for local, arc in pulled:
            zf.write(local, arc)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    log(serial, f"✅ สร้าง backup: {os.path.basename(zip_path)}", Fore.GREEN)
    return zip_path


# ═══════════════════════════════════════════════════════════════════
#  ตัดสินใจชื่อไฟล์ zip จากของที่เจอ
# ═══════════════════════════════════════════════════════════════════
def ordered_names():
    """ลำดับ canonical ของชื่อ (item ก่อน แล้วค่อย pet)"""
    return list(C.ITEM_GET_MAP.values()) + list(C.PET_GET_MAP.values())


def decide_zip_name(found):
    """
    found = set ของชื่อที่เจอ (จาก get-item + get-pet)
    คืนชื่อไฟล์ zip (ไม่รวม .zip) หรือ None ถ้าไม่ต้องจด
    กติกา:
      - ของใน RECORD_ALONE (banana/backpack) เจอเดี่ยวๆ ไม่จด
      - จะจดก็ต่อเมื่อมีของ "strong" อย่างน้อย 1 (เช่น headking/trader)
        หรือมี weak ที่ตั้ง RECORD_ALONE=True
      - ถ้าจด → ใส่ชื่อทุกอย่างที่เจอ ต่อด้วย +
    """
    if not found:
        return None
    order = ordered_names()
    found_ordered = [n for n in order if n in found]

    strong = [n for n in found_ordered if n not in C.RECORD_ALONE]
    forced_weak = [n for n in found_ordered if C.RECORD_ALONE.get(n) is True]

    if strong or forced_weak:
        return "+".join(found_ordered)
    return None


# ═══════════════════════════════════════════════════════════════════
#  STEP: play sequence
# ═══════════════════════════════════════════════════════════════════
def run_play_sequence(device):
    serial = device.serial
    log(serial, "=== PLAY SEQUENCE ===", Fore.GREEN)

    # play1 (กดรูป) → ตำแหน่ง 419,282
    wait_and_click(device, "play1.bmp", post_delay=2.0)
    click_fixed(device, 419, 282, "(หลัง play1)", post_delay=2.0)

    # play2 → play6 (play6 มี timeout 15s, ที่เหลือ 10s แล้วข้าม)
    for i in range(2, 7):
        if i == 6:
            wait_and_click(device, "play6.bmp", timeout=C.PLAY6_TIMEOUT, required=False, post_delay=1.5)
        else:
            wait_and_click(device, f"play{i}.bmp", timeout=C.PLAY_STEP_TIMEOUT, required=False, post_delay=1.5)

    # play7 → play11 (ไม่เจอใน 10 วิ ข้ามไปเลย กันค้างรอ 60 วิ)
    for i in range(7, 12):
        wait_and_click(device, f"play{i}.bmp", timeout=C.PLAY_STEP_TIMEOUT, required=False, post_delay=1.5)

    # หลัง play11 → พิมพ์ชื่อ config + Enter
    log(serial, f"พิมพ์ชื่อ config: {C.CUSTOM_CONFIG_NAME}", Fore.GREEN)
    time.sleep(1.0)
    device.shell(f"input text '{C.CUSTOM_CONFIG_NAME}'")
    time.sleep(1.0)
    device.shell("input keyevent 66")   # KEYCODE_ENTER
    time.sleep(1.5)

    # play12
    wait_and_click(device, "play12.bmp", post_delay=2.0)


def run_event_loops(device):
    """event-back → git-item → ok-gifitem  วน EVENT_LOOP_ROUNDS รอบ"""
    serial = device.serial
    for rnd in range(1, C.EVENT_LOOP_ROUNDS + 1):
        log(serial, f"=== EVENT LOOP รอบ {rnd}/{C.EVENT_LOOP_ROUNDS} ===", Fore.GREEN)
        handle_repeating(device, "event-back.bmp")
        handle_repeating(device, "git-item.bmp")
        handle_repeating(device, "ok-gifitem.bmp")
        handle_repeating(device, "fixnews.bmp")


def run_boxes(device):
    """#รับของ : box1 → box5"""
    log(device.serial, "=== รับของ (box1-5) ===", Fore.GREEN)
    for i in range(1, 6):
        wait_and_click(device, f"box{i}.bmp", post_delay=1.5)


# ═══════════════════════════════════════════════════════════════════
#  STEP: check-ruby  (OCR เลข ruby หลัง box ก่อน get-item)
# ═══════════════════════════════════════════════════════════════════
_ocr_reader = None
_ocr_lock = threading.Lock()


def get_ocr_reader():
    """โหลด EasyOCR ครั้งเดียว (singleton, thread-safe) — โหลดเฉพาะตอนใช้ check-ruby"""
    global _ocr_reader
    if _ocr_reader is None:
        with _ocr_lock:
            if _ocr_reader is None:
                import ssl
                import easyocr
                ssl._create_default_https_context = ssl._create_unverified_context
                print(f"{Fore.YELLOW}[OCR] กำลังโหลดโมเดล EasyOCR (ครั้งแรกครั้งเดียว)...{Style.RESET_ALL}")
                _ocr_reader = easyocr.Reader(['en'], gpu=False)
                print(f"{Fore.GREEN}[OCR] โหลดโมเดลเสร็จ{Style.RESET_ALL}")
    return _ocr_reader


def ocr_ruby_number(img_gray):
    """OCR เลขจาก RUBY_REGION (เฉพาะตัวเลข) → คืน string เช่น '315' หรือ None"""
    if img_gray is None:
        return None
    x, y, w, h = C.RUBY_REGION
    H, W = img_gray.shape[:2]
    if x < 0 or y < 0 or x + w > W or y + h > H:
        return None
    crop = img_gray[y:y + h, x:x + w]
    if crop.size == 0:
        return None
    big = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    otsu = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    reader = get_ocr_reader()
    for candidate in (big, otsu):
        try:
            res = reader.readtext(candidate, allowlist="0123456789", detail=0)
        except Exception:
            res = []
        digits = "".join(re.findall(r"\d+", "".join(res)))
        if digits:
            return digits
    return None


def run_check_ruby(device):
    """หา checkpoint-ruby.bmp; เจอแล้ว OCR เลขที่ RUBY_REGION → คืน string ตัวเลข หรือ None
    (แค่จดจำไว้ ยังไม่ทำอะไร — เอาไปต่อท้ายชื่อ zip ตอน finalize)"""
    serial = device.serial
    log(serial, "=== CHECK-RUBY (เช็ค/OCR เลขก่อน get-item) ===", Fore.GREEN)
    path = img_path("checkpoint-ruby.bmp")
    start = time.time()
    img = None
    seen = False
    while time.time() - start < C.RUBY_CHECK_TIMEOUT:
        if not bot_running:
            return None
        img = fast_screencap(device)
        if ImgSearchADB(img, path):
            seen = True
            break
        time.sleep(0.3)
    if not seen:
        log(serial, f"ไม่เจอ checkpoint-ruby.bmp ใน {C.RUBY_CHECK_TIMEOUT}s → ข้าม check-ruby", Fore.YELLOW)
        return None

    val = ocr_ruby_number(img)
    if val:
        log(serial, f"⭐ check-ruby อ่านเลขได้: {val}", Fore.GREEN)
    else:
        log(serial, "อ่านเลข ruby ไม่ได้ (OCR ว่าง) → ข้าม", Fore.YELLOW)
    return val


# ═══════════════════════════════════════════════════════════════════
#  STEP: get-item  (สุ่มของ)
# ═══════════════════════════════════════════════════════════════════
def run_get_item(device, found):
    """
    item1 → item2 (1 รอบ)
    แล้ววนกด item3/item4 จนเจอ end-item → break
    ระหว่างวน scan รูปใน item-get/ เพื่อ math ของที่ได้ → จดลง found
    เจอ end-item แล้ว: cancel → confirm-item → back1 → back2
    """
    serial = device.serial
    log(serial, "=== GET-ITEM (สุ่มของ) ===", Fore.GREEN)

    wait_and_click(device, "item1.bmp", post_delay=1.5)

    # ถ้าไม่เจอ item2 ครบ 15 วิ → กด backitem1 → backitem2 แล้วข้ามไป get-pet เลย
    if not wait_and_click(device, "item2.bmp", timeout=15, required=False, post_delay=1.5):
        log(serial, "ไม่เจอ item2 ครบ 15 วิ → backitem1 → backitem2 → ข้ามไป get-pet", Fore.YELLOW)
        wait_and_click(device, "backitem1.bmp", post_delay=1.5)
        wait_and_click(device, "backitem2.bmp", post_delay=1.5)
        return

    start = time.time()
    last_item3 = time.time()   # เวลาเจอปุ่ม item3 ล่าสุด (ไว้นับ timeout)
    while time.time() - start < C.LOOP_MAX_SECS:
        if not bot_running:
            return
        img = fast_screencap(device)
        if img is None:
            time.sleep(0.3)
            continue

        # math ของที่สุ่มได้
        for fname, name in C.ITEM_GET_MAP.items():
            if name in found:
                continue
            if ImgSearchADB(img, img_path(fname, C.ITEM_GET_DIR), C.ITEM_MATCH_THRESHOLD):
                found.add(name)
                log(serial, f"⭐ get-item เจอ: {name} ({fname})", Fore.GREEN)

        # end-item → จบ
        if ImgSearchADB(img, img_path("end-item.bmp")):
            log(serial, "เจอ end-item → จบ get-item")
            break

        # กด item3 / item4
        pts3 = ImgSearchADB(img, img_path("item3.bmp"))
        if pts3:
            last_item3 = time.time()
            tap(device, *pts3[0])
            time.sleep(0.6)
        elif time.time() - last_item3 > C.ITEM3_TIMEOUT:
            # ไม่เจอปุ่ม item3 ครบ ITEM3_TIMEOUT วิ → backitem1 → backitem2 → ข้ามไป get-pet
            log(serial, f"ไม่เจอ item3 ครบ {C.ITEM3_TIMEOUT} วิ → backitem1 → backitem2 → ข้ามไป get-pet", Fore.YELLOW)
            wait_and_click(device, "backitem1.bmp", post_delay=1.5)
            wait_and_click(device, "backitem2.bmp", post_delay=1.5)
            return
        pts4 = ImgSearchADB(img, img_path("item4.bmp"))
        if pts4:
            tap(device, *pts4[0])
            time.sleep(0.6)
        time.sleep(0.3)

    # cancel → confirm-item → back1 → back2
    wait_and_click(device, "cancel.bmp", post_delay=1.5)
    wait_and_click(device, "confirm-item.bmp", post_delay=1.5)
    wait_and_click(device, "back1.bmp", post_delay=1.5)
    wait_and_click(device, "back2.bmp", post_delay=1.5)


# ═══════════════════════════════════════════════════════════════════
#  STEP: get-pet
# ═══════════════════════════════════════════════════════════════════
def run_get_pet(device, found):
    """
    pet1 → pet2 (1 รอบ)
    แล้ววนกด pet3/pet4 จนเจอ end-pet → break
    ระหว่างวน scan รูปใน pet-get/ เพื่อ math (pet-item=trader) → จดลง found แล้ว break ทันที
    """
    serial = device.serial
    log(serial, "=== GET-PET ===", Fore.GREEN)

    wait_and_click(device, "pet1.bmp", post_delay=1.5)
    wait_and_click(device, "pet2.bmp", post_delay=1.5)

    start = time.time()
    while time.time() - start < C.LOOP_MAX_SECS:
        if not bot_running:
            return
        img = fast_screencap(device)
        if img is None:
            time.sleep(0.3)
            continue

        # math เพ็ทที่สุ่มได้ → เจอแล้วจบเลย
        pet_hit = False
        for fname, name in C.PET_GET_MAP.items():
            if ImgSearchADB(img, img_path(fname, C.PET_GET_DIR), C.ITEM_MATCH_THRESHOLD):
                found.add(name)
                log(serial, f"⭐ get-pet เจอ: {name} ({fname}) → จบ", Fore.GREEN)
                pet_hit = True
        if pet_hit:
            break

        # end-pet → จบ  (ถ้ายังไม่มีไฟล์ end-pet.bmp จะใช้ safety cap แทน)
        if ImgSearchADB(img, img_path("end-pet.bmp")):
            log(serial, "เจอ end-pet → จบ get-pet")
            break

        pts3 = ImgSearchADB(img, img_path("pet3.bmp"))
        if pts3:
            tap(device, *pts3[0])
            time.sleep(0.6)
        pts4 = ImgSearchADB(img, img_path("pet4.bmp"))
        if pts4:
            tap(device, *pts4[0])
            time.sleep(3)   # delay หลังกด pet4 แล้วค่อยวนไปกด pet3
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════════════════
#  STEP: จบรอบ → backup / ลบไฟล์
#  (เรียกตอน root เปิดอยู่แล้ว — backup ถ้าเข้าเงื่อนไข แล้วลบ identity files)
# ═══════════════════════════════════════════════════════════════════
def finalize_cycle(device, found, ruby=None):
    serial = device.serial
    zip_name = decide_zip_name(found)
    log(serial, f"ของที่เจอทั้งหมด: {sorted(found) if found else '(ไม่เจอ)'}"
                + (f" | ruby={ruby}" if ruby else ""), Fore.MAGENTA)

    if zip_name:
        # แยกโฟลเดอร์ตามจำนวนชิ้นที่จด (find-1 / find-2 / ...) ถ้าเปิด option
        subdir = None
        if C.SPLIT_BACKUP_BY_COUNT:
            count = len(zip_name.split("+"))
            subdir = f"find-{count}"
            log(serial, f"แยกเก็บโฟลเดอร์: backup/{subdir}/ ({count} ชิ้น)", Fore.CYAN)
        log(serial, f"จะ backup ในชื่อ: {zip_name}.zip", Fore.GREEN)
        export_backup_zip(device, zip_name, ruby, subdir)
        with STATS_LOCK:
            STATS["backups"] += 1
            for n in found:
                STATS["found"][n] = STATS["found"].get(n, 0) + 1
    else:
        log(serial, "ไม่เข้าเงื่อนไขจด → ข้าม backup", Fore.YELLOW)
        with STATS_LOCK:
            STATS["no_match"] += 1

    delete_account_files(device)
    with STATS_LOCK:
        STATS["cycles"] += 1


# ═══════════════════════════════════════════════════════════════════
#  MAIN per-device loop
# ═══════════════════════════════════════════════════════════════════
def process_device(serial_or_device):
    serial = serial_or_device.serial if hasattr(serial_or_device, 'serial') else str(serial_or_device)
    client = AdbClient(host="127.0.0.1", port=5037)
    device = client.device(serial)
    if device is None:
        log(serial, "ERROR: เชื่อมต่อ device ไม่ได้", Fore.RED)
        return

    log(serial, "เริ่มทำงาน...", Fore.GREEN)

    # ── initial clean: เปิด root → ลบ identity files → ปิด root ──
    # ให้เริ่มเกมรอบแรกด้วยบัญชีสะอาด และสถานะ root ปิด (เกมจะไม่เจอ root)
    if step_on("first_loop"):
        try:
            device = enable_root(device)
            device.shell(f"am force-stop {C.PACKAGE}")
            time.sleep(1)
            delete_account_files(device)
            device = disable_root(device)
        except Exception as e:
            log(device.serial, f"init clean error: {e}", Fore.RED)
    else:
        log(device.serial, "ข้าม first_loop (ปิดใน config)", Fore.YELLOW)

    while bot_running:
        try:
            found = set()

            # 1) start packet (ตอนนี้ root ปิดอยู่แล้ว → เกมไม่เจอ root) — ทำเสมอ
            start_game(device)
            time.sleep(15)

            # 2) play sequence
            if step_on("play"):
                run_play_sequence(device)

            # 3) event-back → git-item → ok-gifitem
            if step_on("event"):
                run_event_loops(device)

            # 4) รับของ box1-5
            if step_on("boxes"):
                run_boxes(device)

            # 4.5) check-ruby: OCR เลขก่อน get-item (จดไว้เฉยๆ เอาไปต่อท้ายชื่อ zip)
            ruby = None
            if step_on("check_ruby"):
                ruby = run_check_ruby(device)

            # 5) สุ่มของ get-item
            if step_on("get_item"):
                run_get_item(device, found)

            # 6) get-pet
            if step_on("get_pet"):
                run_get_pet(device, found)

            # 7) ปิดเกม → เปิด root → backup (ถ้าเจอ) + ลบ identity files → ปิด root — ทำเสมอ
            close_app(device)
            device = enable_root(device)
            finalize_cycle(device, found, ruby)
            device = disable_root(device)

            log(device.serial, "จบรอบ → เริ่มใหม่", Fore.GREEN)
            time.sleep(3)

        except Exception as e:
            log(device.serial, f"Error: {e}", Fore.RED)
            time.sleep(5)


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
    if C.USE_MUMU_ROOT:
        MUMU_MANAGER_PATH = find_mumu_manager() or ""
        if MUMU_MANAGER_PATH:
            print(f"{Fore.GREEN}[MuMu] ใช้ MuMuManager: {MUMU_MANAGER_PATH}{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}[MuMu] หา MuMuManager.exe ไม่เจอ! แก้ MUMU_MANAGER ใน config.py{Style.RESET_ALL}")

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


def main():
    global bot_running
    load_runtime_config()
    set_process_priority()
    if not find_adb_executable():
        print(f"{Fore.RED}[ERROR] ไม่เจอ adb.exe{Style.RESET_ALL}")
        return
    devices = discover_devices()
    if not devices:
        print(f"{Fore.RED}[ERROR] ไม่เจอ device{Style.RESET_ALL}")
        return
    print(f"{Fore.GREEN}[OK] เจอ {len(devices)} device: {devices}{Style.RESET_ALL}")

    bot_running = True
    threads = []
    for serial in devices:
        t = threading.Thread(target=process_device, args=(serial,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(2)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bot_running = False
        print(f"{Fore.YELLOW}[STOP] หยุดบอท...{Style.RESET_ALL}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Cookie Run bot")
    parser.add_argument("--cli", action="store_true",
                        help="รันแบบ console ไม่เปิด UI (อ่าน configmain.json แล้วเริ่มเลย)")
    cli_args = parser.parse_args()

    if cli_args.cli:
        main()
    else:
        # default: เปิด UI (gui.py). ถ้าเปิดไม่ได้ → fallback เป็น CLI
        try:
            import gui
            gui.launch()
        except SystemExit:
            raise
        except Exception as e:
            print(f"{Fore.YELLOW}[GUI] เปิด UI ไม่ได้: {e} → รันแบบ CLI แทน{Style.RESET_ALL}")
            main()

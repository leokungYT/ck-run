# -*- coding: utf-8 -*-
"""
web_item.py — สถิติ "set ของที่เจอ" สำหรับ login.py
════════════════════════════════════════════════════════════════════════
เปิดใช้ด้วย config-main.json → steps.web_item = 1

ทุกครั้ง "ก่อนส่งไฟล์ออก" (ก่อน export zip) login.py จะเรียก record() มาจด
ชื่อ set ที่เจอ (เช่น headking+trader+dragon-white) พร้อม member id ([RDNXK5360])
ลงไฟล์ web-item/stats.json (คีย์ด้วย member id → id เดิมมาซ้ำจะอัปเดต ไม่นับซ้ำ)
แล้ว render หน้าเว็บ web-item/index.html ใหม่ทันที

หน้าเว็บจัดกลุ่มตาม "set" (ชื่อของที่เจอรวมกัน) แล้วนับว่าแต่ละ set มีกี่ id
พร้อมโชว์ไอคอนจาก img/item-status/<ชื่อ>.png (เช่น headking.png / trader.png)

  ตัวอย่างชื่อไฟล์ที่ parse ได้:
    (2-2)+headking+trader+dragon-white+[RDNXK5360]
       → set = [headking, trader, dragon-white], id = RDNXK5360
    ถ้าเจอ headking + trader ในชื่อ → นับเป็น "เจอ 1" (1 id ของ set นั้น)
"""
import os
import re
import json
import time
import html
import threading
import subprocess
import webbrowser

try:
    import config as C
except Exception:      # ใช้แบบ standalone ก็ยังได้ (ไม่มี config ก็ข้าม order)
    C = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web-item")
STATS_FILE = os.path.join(WEB_DIR, "stats.json")
HTML_FILE = os.path.join(WEB_DIR, "index.html")
# path ไอคอน (relative จาก web-item/index.html → ../img/item-status/<name>.png)
ICON_REL_DIR = "../img/item-status"
ICON_ABS_DIR = os.path.join(BASE_DIR, "img", "item-status")
# แม็พชื่อ → ไฟล์รูป (แก้ง่ายๆ ผ่านไฟล์นี้ ไม่ต้องแตะโค้ด)
STATUS_CONFIG = os.path.join(BASE_DIR, "config-status.json")
# config-main.json — อ่าน found_dir (id-found) ไว้สแกนไฟล์เก่ามาผสม
MAIN_CONFIG = os.path.join(BASE_DIR, "config-main.json")
DEFAULT_FOUND_DIR = "id-found"

_LOCK = threading.Lock()

# ── regex สำหรับ parse ชื่อไฟล์ ─────────────────────────────────────────
_COUNT_TOKEN = re.compile(r"\(\d+[-/／]\d+\)")          # ป้ายนับ (2-2) (0-1)
_BRACKET = re.compile(r"\[([^\]]*)\]")                  # [RDNXK5360] [315]
_MEMBER_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")     # member id ขึ้นต้นด้วยตัวอักษร (ruby เป็นเลขล้วน)


def canonical_order():
    """ลำดับ canonical ของชื่อ (item ก่อน แล้ว pet) — ใช้จัดเรียงไอคอน"""
    if C is None:
        return []
    try:
        return list(C.ITEM_GET_MAP.values()) + list(C.PET_GET_MAP.values())
    except Exception:
        return []


def parse_name(out_name):
    """แยกชื่อไฟล์ export → (list ชื่อของที่เจอ, member_id หรือ None)

    เช่น '(2-2)+headking+trader+dragon-white+[RDNXK5360]'
         → (['headking','trader','dragon-white'], 'RDNXK5360')
    """
    s = str(out_name)
    # หา member id จาก bracket ที่ "มีตัวอักษร" (ruby [315] เป็นเลขล้วน → ข้าม)
    member = None
    for b in _BRACKET.findall(s):
        b = b.strip()
        if _MEMBER_ID.match(b):
            member = b            # เอาตัวท้ายสุด (ชื่อไฟล์เอา [ID] ไว้ท้าย)
    # เอา bracket + ป้ายนับ ออก เหลือแต่ชื่อของ
    s = _BRACKET.sub("", s)
    s = _COUNT_TOKEN.sub("", s)
    parts = []
    for p in s.split("+"):
        p = p.strip()
        if p and p not in parts:
            parts.append(p)
    return parts, member


def _order_names(names):
    """เรียงชื่อตาม canonical (ของที่ไม่รู้จักไปต่อท้าย ตามลำดับเดิม)"""
    order = canonical_order()
    known = [n for n in order if n in names]
    rest = [n for n in names if n not in order]
    return known + rest


def _set_key(names):
    return "+".join(_order_names(names))


# ── โหลด/เซฟ stats ──────────────────────────────────────────────────────
def _load():
    if not os.path.exists(STATS_FILE):
        return {"ids": {}}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "ids" not in data:
            data = {"ids": {}}
        return data
    except Exception:
        return {"ids": {}}


def _save(data):
    os.makedirs(WEB_DIR, exist_ok=True)
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATS_FILE)


# ── สแกนไฟล์ .zip ที่มีอยู่แล้วใน id-found มาผสม (เผื่อ stats.json ถูกลบ) ──
_ID_SUFFIX_RE = re.compile(r"_\d+$")   # ตัด suffix กันชื่อชนท้ายไฟล์ (…_2, …_3)


def _found_dir():
    """path ของโฟลเดอร์ id-found (อ่านจาก config-main.json → found_dir)"""
    fd = DEFAULT_FOUND_DIR
    try:
        with open(MAIN_CONFIG, "r", encoding="utf-8") as f:
            fd = (json.load(f).get("found_dir") or fd)
    except Exception:
        pass
    return fd if os.path.isabs(fd) else os.path.join(BASE_DIR, fd)


def _backup_dir():
    """path ของโฟลเดอร์ backup-id (อ่านจาก config-main.json → backup_id_dir)"""
    bd = "backup-id"
    try:
        with open(MAIN_CONFIG, "r", encoding="utf-8") as f:
            bd = (json.load(f).get("backup_id_dir") or bd)
    except Exception:
        pass
    return bd if os.path.isabs(bd) else os.path.join(BASE_DIR, bd)


def scan_found():
    """สแกนไฟล์ .zip ทั้งหมดใน id-found และ backup-id (รวม part-XXXX ย่อย) → dict {key: rec}"""
    recs = {}
    bases = []
    # โฟลเดอร์ id-found
    fd = _found_dir()
    if os.path.isdir(fd):
        bases.append(fd)
    # โฟลเดอร์ backup-id
    bd = _backup_dir()
    if os.path.isdir(bd):
        bases.append(bd)

    for base in bases:
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.lower().endswith(".zip"):
                    continue
                nm = _ID_SUFFIX_RE.sub("", os.path.splitext(fn)[0])   # ตัด _2/_3 ท้าย
                names, member = parse_name(nm)
                if not names:
                    continue
                key = member or "_file_" + os.path.relpath(os.path.join(root, fn), base)
                recs[key] = {
                    "names": _order_names(names),
                    "dir": os.path.basename(base),
                    "ts": 0
                }
    return recs


def reset():
    """เริ่มชุดข้อมูลใหม่ — ล้าง stats.json และโหลดข้อมูลจากไฟล์จริงที่ยังอยู่ในเครื่องเท่านั้น (id-found + backup-id)"""
    with _LOCK:
        existing = scan_found()
        data = {"ids": existing}
        _save(data)
        try:
            render(data)
        except Exception:
            pass
        return len(existing)


def record(out_name, out_dir=""):
    """จด set ที่เจอ (จากชื่อไฟล์ export) ลง stats.json แล้ว render เว็บใหม่

    - ไม่มีชื่อของเลย (เช่น not-found เหลือแค่ [ID]) → ข้าม ไม่จด
    - id เดิมมาซ้ำ → อัปเดต record เดิม (ไม่นับซ้ำ)
    คืน True ถ้าจด/อัปเดต
    """
    names, member = parse_name(out_name)
    if not names:
        return False
    names = _order_names(names)
    key = member or f"_noid_{_set_key(names)}"
    with _LOCK:
        data = _load()
        data["ids"][key] = {
            "names": names,
            "dir": str(out_dir),
            "ts": int(time.time()),
        }
        _save(data)
        try:
            render(data)
        except Exception:
            pass
    return True


# ── สรุปเป็น set → นับ id ────────────────────────────────────────────────
def summarize(data=None):
    """คืน list ของ set เรียงจากจำนวน id มาก→น้อย
    [{'names': [...], 'count': N, 'ids': [...]}, ...]"""
    if data is None:
        data = _load()
    groups = {}
    for mid, rec in data.get("ids", {}).items():
        names = rec.get("names") or []
        if not names:
            continue
        key = _set_key(names)
        g = groups.setdefault(key, {"names": _order_names(names), "count": 0, "ids": []})
        g["count"] += 1
        if not str(mid).startswith("_noid_"):
            g["ids"].append(mid)
    rows = list(groups.values())
    rows.sort(key=lambda g: (-g["count"], g["names"]))
    return rows


# ── render HTML ─────────────────────────────────────────────────────────
_BADGE_COLORS = ["#2f7bf6", "#12b886", "#12b886", "#f08c00",
                 "#e8590c", "#e03131", "#ae3ec9", "#1098ad"]


def _norm(s):
    """normalize ชื่อ: ตัดพิมพ์เล็ก-ใหญ่/เว้นวรรค/ขีด ออก
    → 'Trader'=='trader', 'white dragon'=='white-dragon'=='white_dragon'"""
    return re.sub(r"[\s_-]+", "-", str(s).strip().lower())


# cache แม็พชื่อ→รูป (โหลดใหม่เองเมื่อไฟล์ config-status.json ถูกแก้)
_STATUS_CACHE = {"mtime": -1, "map": {}}


def _read_status_map():
    m = {}
    try:
        with open(STATUS_CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
        for section in ("treasure", "pet", "item"):   # รับได้ทั้ง 3 หมวด
            for name, png in (d.get(section) or {}).items():
                if isinstance(png, str) and png.strip():
                    m[_norm(name)] = png.strip()
    except Exception:
        pass
    return m


def load_status_map():
    try:
        mt = os.path.getmtime(STATUS_CONFIG)
    except OSError:
        mt = -1
    if _STATUS_CACHE["mtime"] != mt:
        _STATUS_CACHE["map"] = _read_status_map()
        _STATUS_CACHE["mtime"] = mt
    return _STATUS_CACHE["map"]


def _file_in_icons(fname):
    return bool(fname) and os.path.exists(os.path.join(ICON_ABS_DIR, fname))


def resolve_icon(name):
    """คืนชื่อไฟล์รูปของ name (หรือ None ถ้าไม่เจอ)
    ลำดับ: config-status.json → <name>.png → <normalized>.png"""
    png = load_status_map().get(_norm(name))
    if _file_in_icons(png):
        return png
    for cand in (f"{name}.png", f"{_norm(name)}.png"):
        if _file_in_icons(cand):
            return cand
    return None


def _icon_html(name):
    safe = html.escape(name)
    png = resolve_icon(name)
    if png:
        src = f"{ICON_REL_DIR}/{html.escape(png)}"
        return (f'<span class="icon"><img src="{src}" alt="{safe}" '
                f'title="{safe}" loading="lazy"></span>')
    # ไม่มีรูป → กล่อง placeholder โชว์ชื่อย่อ (เพิ่มรูปได้ผ่าน config-status.json)
    short = html.escape(name[:3].upper())
    return (f'<span class="icon ph" title="{safe} (ยังไม่มีรูป)">{short}</span>')


def _row_html(idx, g):
    color = _BADGE_COLORS[idx % len(_BADGE_COLORS)]
    icons = "".join(_icon_html(n) for n in g["names"])
    names_txt = html.escape(" + ".join(g["names"]))
    norm_names = ",".join(_norm(n) for n in g["names"])
    ids_txt = html.escape(", ".join(sorted(g["ids"]))) or "—"
    cnt = g["count"]
    return f'''    <div class="row" data-items="{norm_names}">
      <details>
        <summary>
          <span class="tri"></span>
          <span class="icons">{icons}</span>
          <span class="setname">{names_txt}</span>
          <span class="badge" style="background:{color}">{cnt} ID</span>
        </summary>
        <div class="ids">{ids_txt}</div>
      </details>
    </div>'''


def render(data=None):
    """สร้าง web-item/index.html แบบ static สมบูรณ์จาก Python (ไม่ต้องโหลด JS ใดๆ)"""
    if data is None:
        data = _load()
    rows = summarize(data)
    total_ids = sum(g["count"] for g in rows)
    total_sets = len(rows)
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    if rows:
        body = "\n".join(_row_html(i, g) for i, g in enumerate(rows))
    else:
        body = ('    <div class="empty">ยังไม่มีข้อมูล — '
                'เปิด steps.web_item = 1 แล้วรัน login.py เพื่อเก็บสถิติ</div>')

    doc = _TEMPLATE.format(
        total_ids=total_ids,
        total_sets=total_sets,
        now=html.escape(now),
        rows=body,
    )
    os.makedirs(WEB_DIR, exist_ok=True)
    tmp = HTML_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, HTML_FILE)
    return HTML_FILE


_TEMPLATE = """<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Item Stats — Cookie Run</title>
<style>
  :root {{
    --bg:#0f1117; --card:#171a22; --row:#1c2028; --line:#262b36;
    --text:#e7ebf2; --muted:#98a2b3;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--bg); color:var(--text);
    font-family:"Segoe UI",Roboto,"Noto Sans Thai",system-ui,sans-serif;
  }}
  header {{
    padding:22px 26px 16px; border-bottom:1px solid var(--line);
    position:sticky; top:0; background:var(--bg); z-index:5;
  }}
  h1 {{ margin:0 0 10px; font-size:20px; letter-spacing:.3px; }}
  .stats {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .stat {{
    background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:10px 16px; min-width:120px;
  }}
  .stat .n {{ font-size:24px; font-weight:700; }}
  .stat .l {{ font-size:12px; color:var(--muted); margin-top:2px; }}
  .updated {{ margin-left:auto; align-self:center; color:var(--muted); font-size:12px; }}
  .search-container {{
    padding: 16px 26px 8px;
    max-width: 1100px;
    margin: 0 auto;
  }}
  .search-container input {{
    width: 100%;
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px 18px;
    color: var(--text);
    font-size: 14px;
    outline: none;
    transition: all 0.25s ease;
  }}
  .search-container input:focus {{
    border-color: #2f7bf6;
    box-shadow: 0 0 0 3px rgba(47, 123, 246, 0.15);
  }}
  main {{ padding:8px 26px 60px; max-width:1100px; margin:0 auto; }}
  .row {{ margin:8px 0; }}
  details {{
    background:var(--row); border:1px solid var(--line); border-radius:12px;
    overflow:hidden;
  }}
  summary {{
    list-style:none; cursor:pointer; display:flex; align-items:center;
    gap:14px; padding:12px 16px;
  }}
  summary::-webkit-details-marker {{ display:none; }}
  .tri {{
    width:0; height:0; border-left:7px solid var(--muted);
    border-top:5px solid transparent; border-bottom:5px solid transparent;
    transition:transform .15s; flex:0 0 auto;
  }}
  details[open] .tri {{ transform:rotate(90deg); }}
  .icons {{ display:flex; gap:8px; flex:0 0 auto; }}
  .icon {{
    width:52px; height:52px; border-radius:12px; overflow:hidden;
    display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg,#b48cff33,#7bd3ff33);
    border:1px solid var(--line);
  }}
  .icon img {{ width:100%; height:100%; object-fit:contain; }}
  .icon.ph {{ font-size:12px; font-weight:700; color:var(--muted); }}
  .setname {{ color:var(--muted); font-size:13px; }}
  .badge {{
    margin-left:auto; color:#fff; font-weight:700; font-size:13px;
    padding:6px 14px; border-radius:999px; white-space:nowrap;
  }}
  .ids {{
    padding:10px 16px 14px 50px; color:var(--muted); font-size:12px;
    border-top:1px solid var(--line); word-break:break-all;
  }}
  .empty {{ color:var(--muted); padding:40px; text-align:center; }}
</style>
</head>
<body>
<header>
  <h1>📊 สถิติ Item Set ที่เจอ</h1>
  <div class="stats">
    <div class="stat"><div class="n">{total_ids}</div><div class="l">รวมทั้งหมด (ID)</div></div>
    <div class="stat"><div class="n">{total_sets}</div><div class="l">จำนวน set</div></div>
    <div class="updated">อัปเดตล่าสุด {now}</div>
  </div>
</header>
<div class="search-container">
  <input type="text" id="search-input" placeholder="🔍 ค้นหาเซ็ต เช่น trader+banana+headking" oninput="handleSearchInput()">
</div>
<main id="main-content">
{rows}
</main>
<script>
function filterRows() {{
  const query = document.getElementById("search-input").value.toLowerCase().trim();
  const rows = document.querySelectorAll(".row");
  if (!query) {{
    rows.forEach(r => r.style.display = "");
    return;
  }}
  const terms = query.split("+").map(t => t.trim().replace(/[\s_-]+/g, "-")).filter(t => t.length > 0);
  
  rows.forEach(row => {{
    const itemsAttr = row.getAttribute("data-items") || "";
    const items = itemsAttr.split(",");
    const matchesAll = terms.every(term => {{
      return items.some(item => item.indexOf(term) !== -1);
    }});
    if (matchesAll) {{
      row.style.display = "";
    }} else {{
      row.style.display = "none";
    }}
  }});
}}

function handleSearchInput() {{
  const val = document.getElementById("search-input").value;
  localStorage.setItem("search_query", val);
  filterRows();
}}

document.addEventListener("DOMContentLoaded", () => {{
  const savedQuery = localStorage.getItem("search_query") || "";
  const input = document.getElementById("search-input");
  if (input && savedQuery) {{
    input.value = savedQuery;
    filterRows();
  }}
}});
</script>
</body>
</html>
"""


# ── เปิดหน้าเว็บอัตโนมัติตอนเริ่มรัน (หน้าต่างเล็ก ไม่เต็มจอ) ─────────────
_OPENED = False


def _browser_exe():
    """หา chrome/edge เพื่อเปิดแบบ --app (หน้าต่างเล็ก ไม่มี tab/แถบ)"""
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pfx = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    la = os.environ.get("LOCALAPPDATA", "")
    for c in [
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pfx, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(la, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pfx, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
    ]:
        if c and os.path.exists(c):
            return c
    return None


def open_browser(width=600, height=800, x=60, y=40):
    """เปิด web-item/index.html เป็นหน้าต่างเล็กๆ (ครั้งเดียวต่อ process)
    — ใช้ chrome/edge โหมด --app ถ้ามี ไม่งั้น fallback เปิดเบราว์เซอร์ปกติ"""
    global _OPENED
    if _OPENED:
        return
    if not os.path.exists(HTML_FILE):
        try:
            render()
        except Exception:
            pass
    url = "file:///" + HTML_FILE.replace("\\", "/")
    exe = _browser_exe()
    try:
        if exe:
            flags = {}
            if os.name == "nt":
                flags["creationflags"] = 0x00000008   # DETACHED_PROCESS
            subprocess.Popen(
                [exe, f"--app={url}",
                 f"--window-size={width},{height}",
                 f"--window-position={x},{y}"],
                **flags)
        else:
            webbrowser.open(url)
        _OPENED = True
    except Exception:
        try:
            webbrowser.open(url)
            _OPENED = True
        except Exception:
            pass


# ── CLI: python web_item.py  → ผสม id-found + stats.json แล้ว render (ไม่ล้าง) ──
if __name__ == "__main__":
    data = _load()
    merged = dict(scan_found())        # ไฟล์จริงใน id-found ก่อน
    merged.update(data.get("ids", {}))  # แล้วทับด้วย record สดจาก stats.json
    data = {"ids": merged}
    _save(data)
    out = render(data)
    print(f"rendered: {out}  (id-found + stats.json = {len(merged)} id)")
    for g in summarize(data):
        print(f"  {g['count']:>4}  {' + '.join(g['names'])}")

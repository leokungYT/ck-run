# ═══════════════════════════════════════════════════════════════════
#  config.py  —  ตั้งค่าบอท Cookie Run สำหรับ main.py
# ═══════════════════════════════════════════════════════════════════

# ── Package / Path ของเกม ────────────────────────────────────────────
PACKAGE = "com.devsisters.crg"
DATA_DIR = "/data/data/com.devsisters.crg"

# โฟลเดอร์รูป template
IMG_DIR = "img"
ITEM_GET_DIR = "img/item-get"   # รูปไว้ math ของที่สุ่มได้ (get-item)
PET_GET_DIR = "img/pet-get"     # รูปไว้ math เพ็ทที่สุ่มได้ (get-pet)

# ── ชื่อ config ที่ต้องพิมพ์ตอน play11 ───────────────────────────────
# หลังกด play11 จะมีช่องให้พิมพ์ → พิมพ์ค่านี้แล้วกด Enter
CUSTOM_CONFIG_NAME = "ozx"

# ── จำนวนรอบ event-back → ok-gifitem ─────────────────────────────────
EVENT_LOOP_ROUNDS = 3

# ── เปิด/ปิดแต่ละขั้นตอน (custom-config) ──────────────────────────────
# ค่า default พวกนี้ถูก override ได้ด้วย configmain.json (แก้ผ่าน gui.py)
#   1 = ทำขั้นตอนนี้   |   0 = ข้าม
# หมายเหตุ: start_game + finalize (backup ถ้าเจอ + ลบไฟล์) ทำงานเสมอ
#   ตัวอย่าง: เปิดแค่ get_item=1, get_pet=0 → จบ get-item แล้ว
#             ส่งไฟล์ id (zip) ออกทันทีถ้าเจอ match (ตามกฎ RECORD_ALONE)
STEPS = {
    "first_loop": 1,   # ลบไฟล์ save ก่อนเริ่ม (initial clean)
    "play":       1,   # play sequence (เข้าเกม + พิมพ์ชื่อ config)
    "event":      1,   # event-back / git-item / ok-gifitem loops
    "boxes":      1,   # รับของ box1-5
    "check_ruby": 0,   # เช็ค/OCR เลข ruby หลัง box ก่อน get-item (ต้องใช้ easyocr)
    "get_item":   1,   # สุ่มของ get-item
    "get_pet":    1,   # สุ่มเพ็ท get-pet
}

# ── Check-Ruby (OCR เลขก่อน get-item) ────────────────────────────────
# เปิดด้วย steps.check_ruby = 1 → หลัง box จบจะหา checkpoint-ruby.bmp
# เจอแล้ว OCR ตัวเลขที่ RUBY_REGION เก็บไว้ แล้วเอาไปต่อท้ายชื่อ zip
#   ตัวอย่าง: headking+trader+[NXSGM1082]+[315].zip   (315 = เลข ruby)
RUBY_REGION = (272, 12, 59, 31)   # (x, y, w, h) ตำแหน่งเลขบนจอ
RUBY_CHECK_TIMEOUT = 10           # รอ checkpoint-ruby.bmp โผล่กี่วิ (ไม่เจอ → ข้าม)

# ── แยกไฟล์ backup ตามจำนวนชิ้นที่เจอ (find-1 / find-2 / find-3 ...) ──
# นับจากจำนวนของที่ถูกจดในชื่อไฟล์ (รวม get-item + get-pet)
#   0 = เก็บรวมใน backup/ (เดิม)
#   1 = แยกใส่ backup/find-N/  (N = จำนวนชิ้น เช่น เจอ 2 ชิ้น → backup/find-2/)
SPLIT_BACKUP_BY_COUNT = 0

# ── Performance: ลด priority ของ process กัน UI/Explorer ค้าง ──────────
# รันหลายจอ cv2 จะกิน CPU จนเต็ม ทำให้ Windows Explorer/หน้าต่างอื่นค้าง
#   1 = ตั้ง process เป็น BELOW_NORMAL (บอทสละ CPU ให้ UI, ช้าลงเล็กน้อย)
#   0 = ปกติ
LOW_PRIORITY = 1

# ── Timeout (วินาที) ─────────────────────────────────────────────────
APPEAR_TIMEOUT = 4      # รอรูปโผล่ครั้งแรก (event-back / git-item / ok-gifitem) — ลดจาก 10
ABSENT_SECS = 3         # กดรัวๆ จนไม่เจอรูปครบกี่วิ ถึงไปต่อ — ลดจาก 5
PLAY6_TIMEOUT = 15      # play6 ถ้าไม่เจอใน 15 วิ ให้ข้าม
PLAY_STEP_TIMEOUT = 10  # play2-5 / play7-11 ถ้าไม่เจอใน 10 วิ ให้ข้าม (กันค้างรอ 60 วิ)
DEFAULT_WAIT = 60       # รอ template ปกติสูงสุดกี่วิ
LOOP_MAX_SECS = 120     # safety cap ของลูป item/pet (กันลูปค้างไม่จบ)
ITEM3_TIMEOUT = 15      # ในลูป get-item ถ้าไม่เจอปุ่ม item3 ครบกี่วิ ให้จบลูป

# ── Image match threshold ────────────────────────────────────────────
MATCH_THRESHOLD = 0.8           # ปุ่มทั่วไป
ITEM_MATCH_THRESHOLD = 0.85     # math ของที่สุ่มได้ (item-get / pet-get) เข้มขึ้นกันจำผิด

# ── การ math ชื่อของที่สุ่มได้ ────────────────────────────────────────
# key = ชื่อไฟล์รูปในโฟลเดอร์ (ITEM_GET_DIR / PET_GET_DIR)
# value = ชื่อที่จะเอาไปตั้งชื่อไฟล์ .zip
# ลำดับใน dict = ลำดับการเรียงชื่อในไฟล์ zip (เช่น headking+banana+trader)
ITEM_GET_MAP = {
    "item1.bmp": "backpack",
    "item2.bmp": "headking",
    "item3.bmp": "banana",
    "item4.bmp": "kapok"
}
PET_GET_MAP = {
    "pet-item.bmp": "trader",
}

# ── ของที่ "เจอเดี่ยวๆ ไม่ต้องจด" ────────────────────────────────────
# ของพวกนี้จะถูกจดก็ต่อเมื่อเจอคู่กับของอื่น (เช่น banana+trader)
# ถ้าอยากบังคับให้จดแม้เจอเดี่ยว → ตั้งเป็น True  (ส่วน custom ในคำสั่ง)
#   False = เจอเดี่ยวไม่จด   |   True = เจอเดี่ยวก็จด
RECORD_ALONE = {
    "banana": False,
    "backpack": False,
    "kapok": False
}

# ── เปิด/ปิด root ─────────────────────────────────────────────────────
# MuMu Player ตรวจ root จาก root_permission setting (ไม่ใช่ adb root)
# toggle root_permission ผ่าน MuMuManager มีผล "ทันที (live)" ไม่ต้อง restart
#   root_permission=false → เกมไม่เจอ root (เปิดเกมผ่าน)
#   su -c ยังใช้ได้เสมอจาก adb shell ไม่ว่า root_permission จะ true/false
#
# USE_MUMU_ROOT = True  → เปิด/ปิด root ผ่าน MuMuManager (สำหรับ MuMu)
# USE_MUMU_ROOT = False → ใช้ adb root/unroot (สำหรับ AVD ธรรมดา)
USE_MUMU_ROOT = True

# path ของ MuMuManager.exe (ดูจาก info: nx_main\MuMuManager.exe)
MUMU_MANAGER = r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe"
# index ของ instance — ดูได้จาก:  MuMuManager.exe info -v all
MUMU_INDEX = "2"

# จัดการไฟล์ด้วย su -c หรือไม่
#   True  = MuMu (root ผ่าน su, adb shell ไม่ใช่ root)  ← ต้องเป็น True เมื่อ USE_MUMU_ROOT
#   False = adb root mode (adb shell เป็น root อยู่แล้ว)
USE_SU = True

# เวลารอหลัง adb root/unroot (เฉพาะโหมด adb root, USE_MUMU_ROOT=False) (วินาที)
ROOT_TOGGLE_WAIT = 3

# ── ไฟล์ที่ต้อง backup / ลบ (ต้องใช้ root) ────────────────────────────
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

# ── ดึง member_id มาต่อท้ายชื่อไฟล์ zip ───────────────────────────────
# อ่านจาก Cocos2dxPrefsFile.xml:  <string name="member_id">DPDDH7496</string>
# ชื่อไฟล์จะเป็น  headking+trader+[DPDDH7496]+.zip
MEMBER_ID_FILE = "Cocos2dxPrefsFile.xml"
MEMBER_ID_KEY = "member_id"

# ── โฟลเดอร์เก็บผลลัพธ์ ───────────────────────────────────────────────
BACKUP_DIR = "backup"

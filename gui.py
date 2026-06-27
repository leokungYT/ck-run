# ═══════════════════════════════════════════════════════════════════
#  gui.py — UI ตั้งค่า (custom-config) + ปุ่ม START สำหรับ Cookie Run bot
#  หมายเหตุ: engine จริงอยู่ที่ main.py — ไฟล์นี้แค่แก้ configmain.json
#            แล้วสั่งให้ main.py ทำงาน (เปิด/ปิดแต่ละ step ได้)
#
#  รัน:  python gui.py        (ต้อง pip install customtkinter ก่อน)
#  รัน CLI ปกติ:  python main.py   (อ่าน configmain.json เหมือนกัน)
# ═══════════════════════════════════════════════════════════════════
import os
import time
import threading

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    import customtkinter as ctk
    from tkinter import messagebox
except ImportError:
    raise SystemExit("ต้องติดตั้ง customtkinter ก่อน:  pip install customtkinter")

import config as C   # noqa: F401  (เผื่อใช้ค่า default)
import main as M

# label ของแต่ละ step (เรียงตามลำดับการทำงานใน main.process_device)
STEP_LABELS = [
    ("first_loop", "🧹 First Loop — ลบไฟล์ save ก่อนเริ่ม"),
    ("play",       "▶  Play Sequence — เข้าเกม + พิมพ์ชื่อ config"),
    ("event",      "🎁 Event Loops — event-back / git-item / ok-gifitem"),
    ("boxes",      "📦 รับของ — box1-5"),
    ("check_ruby", "💎 Check-Ruby — OCR เลขก่อน get-item (ต่อท้ายชื่อไฟล์)"),
    ("get_item",   "🎲 Get-Item — สุ่มของ"),
    ("get_pet",    "🐾 Get-Pet — สุ่มเพ็ท"),
]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Cookie Run Bot — Custom Config")
        self.geometry("540x680")
        self.started = False

        M.load_runtime_config()
        self.rt = M.RUNTIME
        self.step_vars = {}

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(1000, self.refresh_stats)

    # ── UI ────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Toolbar
        bar = ctk.CTkFrame(self, height=46, fg_color="#333333", corner_radius=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self.btn_start = ctk.CTkButton(
            bar, text="▶ START", width=92, height=28,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#e53935", hover_color="#c62828", command=self.start)
        self.btn_start.pack(side="left", padx=10, pady=8)

        self.lbl_status = ctk.CTkLabel(bar, text="● พร้อม", text_color="#aaaaaa",
                                       font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_status.pack(side="left", padx=6)

        self.lbl_stats = ctk.CTkLabel(bar, text="รอบ:0  เซฟ:0  ไม่เจอ:0",
                                      text_color="#4caf50", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_stats.pack(side="right", padx=12)

        # Body (scrollable)
        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(8, 4))

        ctk.CTkLabel(body, text="ขั้นตอนที่จะทำงาน (เปิด/ปิด)",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(6, 4))

        steps = self.rt["steps"]
        for key, label in STEP_LABELS:
            var = ctk.BooleanVar(value=bool(steps.get(key, 1)))
            self.step_vars[key] = var
            ctk.CTkSwitch(body, text=label, variable=var).pack(anchor="w", padx=8, pady=4)

        ctk.CTkLabel(
            body,
            text="💡 start เกม + backup(ถ้าเจอ) + ลบไฟล์ ทำงานเสมอ\n"
                 "    เปิดแค่ get-item → จบ get-item แล้วส่งไฟล์ออกทันทีถ้าเจอ match",
            font=ctk.CTkFont(size=11), text_color="gray", justify="left").pack(anchor="w", padx=8, pady=(2, 6))

        ctk.CTkFrame(body, height=2, fg_color="gray30").pack(fill="x", pady=10)

        # Event rounds
        fr = ctk.CTkFrame(body, fg_color="transparent")
        fr.pack(fill="x", pady=4)
        ctk.CTkLabel(fr, text="จำนวนรอบ Event Loop:").pack(side="left")
        self.ent_rounds = ctk.CTkEntry(fr, width=70)
        self.ent_rounds.insert(0, str(self.rt["event_rounds"]))
        self.ent_rounds.pack(side="left", padx=8)

        # Config name (play11)
        fr2 = ctk.CTkFrame(body, fg_color="transparent")
        fr2.pack(fill="x", pady=4)
        ctk.CTkLabel(fr2, text="ชื่อ config (พิมพ์หลัง play11):").pack(side="left")
        self.ent_cfgname = ctk.CTkEntry(fr2, width=140)
        self.ent_cfgname.insert(0, str(self.rt["config_name"]))
        self.ent_cfgname.pack(side="left", padx=8)

        # แยกไฟล์ backup ตามจำนวนชิ้นที่เจอ (find-1 / find-2 / ...)
        self.split_var = ctk.BooleanVar(value=bool(self.rt.get("split_by_count", 0)))
        ctk.CTkSwitch(body, text="📂 แยก backup ตามจำนวนที่เจอ (find-1 / find-2 / find-3 ...)",
                      variable=self.split_var).pack(anchor="w", padx=8, pady=(8, 2))

        btnrow = ctk.CTkFrame(body, fg_color="transparent")
        btnrow.pack(anchor="w", pady=12)
        ctk.CTkButton(btnrow, text="💾 บันทึก Config", fg_color="#2cc985",
                      hover_color="#229f69", command=self.save).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btnrow, text="🔄 โหลดใหม่จากไฟล์", fg_color="#3b8ed0",
                      hover_color="#2f72a8", command=self.reload_from_disk).pack(side="left")

        ctk.CTkLabel(body, text="🏆 ของที่เจอ (สะสม)",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(6, 2))
        self.found_box = ctk.CTkTextbox(body, height=120,
                                        font=ctk.CTkFont(family="Consolas", size=11))
        self.found_box.pack(fill="x")
        self.found_box.configure(state="disabled")

    # ── helpers ───────────────────────────────────────────────────────
    def _log(self, msg):
        # พิมพ์ลง console เท่านั้น — ไม่เขียน widget จาก background thread
        # (Tkinter ไม่ thread-safe → เคยทำให้ GUI ค้าง Not Responding)
        try:
            print(time.strftime("[%H:%M:%S] ") + msg)
        except Exception:
            pass

    def _collect(self):
        steps = {k: (1 if v.get() else 0) for k, v in self.step_vars.items()}
        try:
            rounds = int(self.ent_rounds.get())
        except ValueError:
            rounds = self.rt["event_rounds"]
        name = self.ent_cfgname.get().strip() or self.rt["config_name"]
        split = 1 if self.split_var.get() else 0
        return steps, rounds, name, split

    def _save_to_disk(self):
        steps, rounds, name, split = self._collect()
        M.save_runtime_config(steps, rounds, name, split)
        M.load_runtime_config()      # reload → push เข้า main.RUNTIME / C
        self.rt = M.RUNTIME

    def save(self):
        self._save_to_disk()
        self._log("✅ บันทึก configmain.json แล้ว")
        enabled = [k for k, v in self.rt["steps"].items() if v]
        self._log(f"step ที่เปิด: {enabled}")

    def reload_from_disk(self):
        """ดึงค่าจาก configmain.json มาอัปเดตสวิตช์/ช่องกรอก (เผื่อแก้ไฟล์ด้วยมือ)"""
        M.load_runtime_config()
        self.rt = M.RUNTIME
        steps = self.rt["steps"]
        for key, var in self.step_vars.items():
            var.set(bool(steps.get(key, 1)))
        self.ent_rounds.delete(0, "end")
        self.ent_rounds.insert(0, str(self.rt["event_rounds"]))
        self.ent_cfgname.delete(0, "end")
        self.ent_cfgname.insert(0, str(self.rt["config_name"]))
        self.split_var.set(bool(self.rt.get("split_by_count", 0)))
        enabled = [k for k, v in steps.items() if v]
        self._log(f"โหลด configmain.json ใหม่ → step ที่เปิด: {enabled}")

    # ── START ─────────────────────────────────────────────────────────
    def start(self):
        if self.started:
            self._log("บอททำงานอยู่แล้ว")
            return
        self._save_to_disk()   # auto-save ก่อน start เสมอ

        if not M.find_adb_executable():
            messagebox.showerror("ADB", "ไม่เจอ adb.exe", parent=self)
            return

        self._log("กำลังค้นหา device...")
        self.started = True
        self.btn_start.configure(state="disabled", text="⏳ RUNNING", fg_color="#555555")
        self.lbl_status.configure(text="● กำลังทำงาน", text_color="#4caf50")

        def worker():
            try:
                devices = M.discover_devices()
            except Exception as e:
                self._log(f"❌ discover error: {e}")
                return
            if not devices:
                self._log("❌ ไม่เจอ device")
                return
            self._log(f"เจอ {len(devices)} device: {devices}")
            M.bot_running = True
            for s in devices:
                threading.Thread(target=M.process_device, args=(s,), daemon=True).start()
                self._log(f"🚀 เริ่ม bot: {s}")
                time.sleep(2)

        threading.Thread(target=worker, daemon=True).start()

    # ── stats poller ──────────────────────────────────────────────────
    def refresh_stats(self):
        try:
            with M.STATS_LOCK:
                cycles = M.STATS["cycles"]
                backups = M.STATS["backups"]
                no_match = M.STATS["no_match"]
                found = dict(M.STATS["found"])
            self.lbl_stats.configure(text=f"รอบ:{cycles}  เซฟ:{backups}  ไม่เจอ:{no_match}")
            self.found_box.configure(state="normal")
            self.found_box.delete("1.0", "end")
            for name, cnt in sorted(found.items()):
                self.found_box.insert("end", f"{name}: {cnt}\n")
            self.found_box.configure(state="disabled")
        except Exception:
            pass
        self.after(1500, self.refresh_stats)

    def on_close(self):
        if self.started and not messagebox.askokcancel("ออก", "หยุดบอทและปิดโปรแกรม?", parent=self):
            return
        M.bot_running = False
        self.destroy()


def launch():
    """เปิดหน้าต่าง UI (เรียกจาก main.py ได้ด้วย)"""
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    launch()

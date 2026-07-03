"""
login-gui.py — GUI ขนาดเล็กสำหรับ login.py (login-refresh batch)
ใช้ customtkinter | ปุ่ม Start/Stop + Setup Config (แก้ config-main.json)
สไตล์เดียวกับ gui.py ของ main.py แต่ผูกกับ step ของ login
"""
import os, sys, json, glob, threading, time
import customtkinter as ctk

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── paths ──
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config-main.json")

# ── default steps (ตรงกับ login.py DEFAULTS) ──
DEFAULT_STEPS = {
    "clean": 1, "restore": 1, "event": 1, "box": 1,
    "maxgacha": 1, "maxpet": 1, "export": 1,
}
STEP_LABELS = {
    "clean":    "ลบข้อมูลเดิมก่อน restore",
    "restore":  "Restore (push ไฟล์จาก input-id)",
    "event":    "Event Loop (login)",
    "box":      "รับของ Box (box1-5)",
    "maxgacha": "Max Gacha (สุ่ม item)",
    "maxpet":   "Max Pet (สุ่มจนเจอ trader)",
    "export":   "Export",
}
# ค่า default ที่ GUI จัดการ (นอกจาก steps) — path ต่างๆ ไม่แตะ เก็บไว้ในไฟล์ตามเดิม
GUI_DEFAULTS = {"event_rounds": 2, "start_wait": 15}


def load_config():
    cfg = {"steps": dict(DEFAULT_STEPS), **GUI_DEFAULTS}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k, v in (d.get("steps") or {}).items():
                key = k.replace("-", "_")
                if key in cfg["steps"]:
                    cfg["steps"][key] = 1 if v else 0
            cfg["event_rounds"] = int(d.get("event_rounds", cfg["event_rounds"]))
            cfg["start_wait"] = int(d.get("start_wait", cfg["start_wait"]))
    except Exception:
        pass
    return cfg


def save_config(managed):
    """merge ทับเฉพาะ field ที่ GUI จัดการ (steps/event_rounds/start_wait)
    เก็บ key อื่นในไฟล์เดิมไว้ (input_dir / output_dir / failed_dir / claim_dir / config_name)"""
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data.update(managed)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
#  Setup Config Window
# ═══════════════════════════════════════════════════════════════════
class ConfigWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("⚙  Setup Config (login)")
        self.geometry("340x500")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        cfg = load_config()

        ctk.CTkLabel(self, text="ตั้งค่าขั้นตอน login", font=("Segoe UI", 15, "bold")).pack(pady=(14, 6))

        # ── step toggles ──
        self.step_vars = {}
        frame_steps = ctk.CTkFrame(self, fg_color="transparent")
        frame_steps.pack(fill="x", padx=16, pady=(0, 8))
        for key in DEFAULT_STEPS:
            var = ctk.BooleanVar(value=bool(cfg["steps"].get(key, 0)))
            self.step_vars[key] = var
            ctk.CTkSwitch(frame_steps, text=STEP_LABELS.get(key, key),
                          variable=var, font=("Segoe UI", 12),
                          switch_width=40, switch_height=20).pack(anchor="w", pady=3)

        ctk.CTkFrame(self, height=1, fg_color=("gray75", "gray35")).pack(fill="x", padx=16, pady=6)

        # ── event rounds ──
        ctk.CTkLabel(self, text="Event Loop (รอบ)", font=("Segoe UI", 12)).pack(anchor="w", padx=20)
        self.rounds_entry = ctk.CTkEntry(self, width=80, font=("Segoe UI", 12))
        self.rounds_entry.insert(0, str(cfg["event_rounds"]))
        self.rounds_entry.pack(anchor="w", padx=20, pady=(2, 8))

        # ── start wait ──
        ctk.CTkLabel(self, text="รอหลัง start packet (วินาที)", font=("Segoe UI", 12)).pack(anchor="w", padx=20)
        self.wait_entry = ctk.CTkEntry(self, width=80, font=("Segoe UI", 12))
        self.wait_entry.insert(0, str(cfg["start_wait"]))
        self.wait_entry.pack(anchor="w", padx=20, pady=(2, 8))

        ctk.CTkButton(self, text="💾  บันทึก", font=("Segoe UI", 13, "bold"),
                      height=36, corner_radius=8, command=self._save).pack(pady=(14, 10))

    def _save(self):
        steps = {k: (1 if v.get() else 0) for k, v in self.step_vars.items()}
        try:
            rounds = max(1, int(self.rounds_entry.get()))
        except ValueError:
            rounds = GUI_DEFAULTS["event_rounds"]
        try:
            wait = max(0, int(self.wait_entry.get()))
        except ValueError:
            wait = GUI_DEFAULTS["start_wait"]
        save_config({
            "steps": steps,
            "event_rounds": rounds,
            "start_wait": wait,
        })
        self.destroy()


# ═══════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("🍪 Cookie Run - Login")
        self.geometry("300x190")
        self.resizable(False, False)

        self._bot_thread = None
        self._running = False

        ctk.CTkLabel(self, text="Cookie Run - Login", font=("Segoe UI", 16, "bold")).pack(pady=(16, 10))

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(pady=(0, 10))

        self.start_btn = ctk.CTkButton(
            bf, text="▶  Start", width=120, height=38,
            font=("Segoe UI", 13, "bold"), corner_radius=8,
            fg_color="#2ecc71", hover_color="#27ae60",
            command=self._toggle_bot,
        )
        self.start_btn.pack(side="left", padx=6)

        ctk.CTkButton(
            bf, text="⚙  Config", width=120, height=38,
            font=("Segoe UI", 13, "bold"), corner_radius=8,
            fg_color="#636e72", hover_color="#535c60",
            command=self._open_config,
        ).pack(side="left", padx=6)

        self.status = ctk.CTkLabel(self, text="หยุดอยู่ — วาง .zip ใน input-id/",
                                   font=("Segoe UI", 11), text_color=("gray50", "gray60"))
        self.status.pack()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── bot control ──
    def _toggle_bot(self):
        if self._running:
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        self._running = True
        self.start_btn.configure(text="⏹  Stop", fg_color="#e74c3c", hover_color="#c0392b")
        self.status.configure(text="กำลังเริ่ม...", text_color=("#2ecc71", "#2ecc71"))

        import login as LG
        LG.load_login_config()
        LG.M.set_process_priority()

        def _run():
            try:
                if not LG.M.find_adb_executable():
                    self.after(0, lambda: self._show_status("ไม่เจอ adb.exe", "#e74c3c"))
                    return

                input_dir = LG.LOGIN["input_dir"]
                os.makedirs(input_dir, exist_ok=True)
                os.makedirs(LG.LOGIN["claim_dir"], exist_ok=True)
                pending = (glob.glob(os.path.join(input_dir, "*.zip"))
                           + glob.glob(os.path.join(LG.LOGIN["claim_dir"], "*", "*.zip")))
                if not pending:
                    self.after(0, lambda: self._show_status(f"ไม่มี .zip ใน {input_dir}/", "#e67e22"))
                    return

                LG.STATS["done"] = 0
                LG.STATS["fail"] = 0
                LG.M.bot_running = True
                devices = LG.M.discover_devices()
                if not devices:
                    self.after(0, lambda: self._show_status("ไม่เจอ device", "#e74c3c"))
                    return

                self.after(0, lambda: self._show_status(
                    f"ทำงาน ({len(devices)} device | ~{len(pending)} บัญชี)", "#2ecc71"))
                threads = []
                for serial in devices:
                    t = threading.Thread(target=LG.worker, args=(serial, input_dir), daemon=True)
                    t.start()
                    threads.append(t)
                    time.sleep(2)
                for t in threads:
                    t.join()

                done, fail = LG.STATS["done"], LG.STATS["fail"]
                self.after(0, lambda: self._show_status(
                    f"เสร็จ — สำเร็จ {done} | ล้มเหลว {fail}", "#2ecc71"))
            except Exception as e:
                self.after(0, lambda: self._show_status(f"Error: {e}", "#e74c3c"))
            finally:
                self._running = False
                self.after(0, self._reset_btn)

        self._bot_thread = threading.Thread(target=_run, daemon=True)
        self._bot_thread.start()

    def _stop_bot(self):
        try:
            import login as LG
            LG.M.bot_running = False
        except Exception:
            pass
        self._running = False
        self._reset_btn()
        self.status.configure(text="กำลังหยุด (รอบัญชีปัจจุบันจบก่อน)...",
                              text_color=("#e67e22", "#e67e22"))

    def _reset_btn(self):
        self.start_btn.configure(text="▶  Start", fg_color="#2ecc71", hover_color="#27ae60")

    def _show_status(self, text, color):
        self.status.configure(text=text, text_color=(color, color))

    def _open_config(self):
        ConfigWindow(self)

    def _on_close(self):
        try:
            import login as LG
            LG.M.bot_running = False
        except Exception:
            pass
        self.after(300, self.destroy)


if __name__ == "__main__":
    App().mainloop()

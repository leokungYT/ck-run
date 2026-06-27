"""
gui.py — GUI ขนาดเล็กสำหรับบอท Cookie Run
ใช้ customtkinter | ปุ่ม Start/Stop + Setup Config
"""
import os, sys, json, threading, time
import customtkinter as ctk

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── paths ──
CONFIGMAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configmain.json")

# ── default config (ตรงกับ config.py) ──
DEFAULT_STEPS = {
    "first_loop": 1, "play": 1, "event": 1, "boxes": 1,
    "check_ruby": 0, "get_item": 1, "get_pet": 1,
}
STEP_LABELS = {
    "first_loop": "ลบไฟล์ตอนเริ่ม",
    "play":       "Play Sequence",
    "event":      "Event Loop",
    "boxes":      "รับของ Box",
    "check_ruby": "Check Ruby (OCR)",
    "get_item":   "สุ่มของ Get-Item",
    "get_pet":    "สุ่มเพ็ท Get-Pet",
}


def load_config():
    cfg = {
        "steps": dict(DEFAULT_STEPS),
        "event_rounds": 3,
        "config_name": "ozx",
        "split_by_count": 0,
    }
    try:
        if os.path.exists(CONFIGMAIN):
            with open(CONFIGMAIN, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k, v in (d.get("steps") or {}).items():
                key = k.replace("-", "_")
                if key in cfg["steps"]:
                    cfg["steps"][key] = 1 if v else 0
            cfg["event_rounds"] = int(d.get("event_rounds", cfg["event_rounds"]))
            cfg["config_name"] = str(d.get("config_name", cfg["config_name"])).strip() or cfg["config_name"]
            cfg["split_by_count"] = 1 if d.get("split_by_count") else 0
    except Exception:
        pass
    return cfg


def save_config(cfg):
    with open(CONFIGMAIN, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
#  Setup Config Window
# ═══════════════════════════════════════════════════════════════════
class ConfigWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("⚙  Setup Config")
        self.geometry("320x520")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        cfg = load_config()

        # ── header ──
        ctk.CTkLabel(self, text="ตั้งค่าขั้นตอน", font=("Segoe UI", 15, "bold")).pack(pady=(14, 6))

        # ── step toggles ──
        self.step_vars = {}
        frame_steps = ctk.CTkFrame(self, fg_color="transparent")
        frame_steps.pack(fill="x", padx=16, pady=(0, 8))
        for key in DEFAULT_STEPS:
            var = ctk.BooleanVar(value=bool(cfg["steps"].get(key, 0)))
            self.step_vars[key] = var
            sw = ctk.CTkSwitch(frame_steps, text=STEP_LABELS.get(key, key),
                               variable=var, font=("Segoe UI", 12),
                               switch_width=40, switch_height=20)
            sw.pack(anchor="w", pady=3)

        # ── separator ──
        ctk.CTkFrame(self, height=1, fg_color=("gray75", "gray35")).pack(fill="x", padx=16, pady=6)

        # ── config name ──
        ctk.CTkLabel(self, text="ชื่อ Config (พิมพ์ตอน play11)", font=("Segoe UI", 12)).pack(anchor="w", padx=20)
        self.name_entry = ctk.CTkEntry(self, width=200, font=("Segoe UI", 12))
        self.name_entry.insert(0, cfg["config_name"])
        self.name_entry.pack(padx=20, pady=(2, 8))

        # ── event rounds ──
        ctk.CTkLabel(self, text="Event Loop (รอบ)", font=("Segoe UI", 12)).pack(anchor="w", padx=20)
        self.rounds_entry = ctk.CTkEntry(self, width=80, font=("Segoe UI", 12))
        self.rounds_entry.insert(0, str(cfg["event_rounds"]))
        self.rounds_entry.pack(anchor="w", padx=20, pady=(2, 8))

        # ── split by count ──
        self.split_var = ctk.BooleanVar(value=bool(cfg["split_by_count"]))
        ctk.CTkSwitch(self, text="แยกโฟลเดอร์ตามจำนวนชิ้น", variable=self.split_var,
                       font=("Segoe UI", 12), switch_width=40, switch_height=20).pack(anchor="w", padx=20, pady=4)

        # ── save button ──
        ctk.CTkButton(self, text="💾  บันทึก", font=("Segoe UI", 13, "bold"),
                       height=36, corner_radius=8,
                       command=self._save).pack(pady=(14, 10))

    def _save(self):
        steps = {k: (1 if v.get() else 0) for k, v in self.step_vars.items()}
        try:
            rounds = max(1, int(self.rounds_entry.get()))
        except ValueError:
            rounds = 3
        cfg = {
            "steps": steps,
            "event_rounds": rounds,
            "config_name": self.name_entry.get().strip() or "ozx",
            "split_by_count": 1 if self.split_var.get() else 0,
        }
        save_config(cfg)
        self.destroy()


# ═══════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("🍪 Cookie Run Bot")
        self.geometry("280x180")
        self.resizable(False, False)

        self._bot_thread = None
        self._running = False

        # ── title ──
        ctk.CTkLabel(self, text="Cookie Run Bot", font=("Segoe UI", 16, "bold")).pack(pady=(16, 10))

        # ── buttons frame ──
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(pady=(0, 10))

        self.start_btn = ctk.CTkButton(
            bf, text="▶  Start", width=110, height=38,
            font=("Segoe UI", 13, "bold"), corner_radius=8,
            fg_color="#2ecc71", hover_color="#27ae60",
            command=self._toggle_bot,
        )
        self.start_btn.pack(side="left", padx=6)

        ctk.CTkButton(
            bf, text="⚙  Config", width=110, height=38,
            font=("Segoe UI", 13, "bold"), corner_radius=8,
            fg_color="#636e72", hover_color="#535c60",
            command=self._open_config,
        ).pack(side="left", padx=6)

        # ── status ──
        self.status = ctk.CTkLabel(self, text="หยุดอยู่", font=("Segoe UI", 11),
                                    text_color=("gray50", "gray60"))
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
        self.status.configure(text="กำลังทำงาน...", text_color=("#2ecc71", "#2ecc71"))

        import main as M
        M.load_runtime_config()
        M.set_process_priority()

        def _run():
            try:
                if not M.find_adb_executable():
                    self.after(0, lambda: self._show_status("ไม่เจอ adb.exe", "#e74c3c"))
                    self._running = False
                    self.after(0, self._reset_btn)
                    return
                devices = M.discover_devices()
                if not devices:
                    self.after(0, lambda: self._show_status("ไม่เจอ device", "#e74c3c"))
                    self._running = False
                    self.after(0, self._reset_btn)
                    return

                self.after(0, lambda: self._show_status(f"ทำงาน ({len(devices)} device)", "#2ecc71"))
                M.bot_running = True
                threads = []
                for serial in devices:
                    t = threading.Thread(target=M.process_device, args=(serial,), daemon=True)
                    t.start()
                    threads.append(t)
                    time.sleep(2)
                for t in threads:
                    t.join()
            except Exception as e:
                self.after(0, lambda: self._show_status(f"Error: {e}", "#e74c3c"))
            finally:
                self._running = False
                self.after(0, self._reset_btn)

        self._bot_thread = threading.Thread(target=_run, daemon=True)
        self._bot_thread.start()

    def _stop_bot(self):
        try:
            import main as M
            M.bot_running = False
        except Exception:
            pass
        self._running = False
        self._reset_btn()
        self.status.configure(text="กำลังหยุด...", text_color=("#e67e22", "#e67e22"))
        self.after(2000, lambda: self.status.configure(text="หยุดอยู่", text_color=("gray50", "gray60")))

    def _reset_btn(self):
        self.start_btn.configure(text="▶  Start", fg_color="#2ecc71", hover_color="#27ae60")

    def _show_status(self, text, color):
        self.status.configure(text=text, text_color=(color, color))

    def _open_config(self):
        ConfigWindow(self)

    def _on_close(self):
        try:
            import main as M
            M.bot_running = False
        except Exception:
            pass
        self.after(300, self.destroy)


if __name__ == "__main__":
    App().mainloop()

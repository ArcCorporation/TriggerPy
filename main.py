import tkinter as tk
from tkinter import ttk
import logging
import time
from datetime import datetime
from pathlib import Path
from Helpers.debugger import DebugFrame, TkinterHandler
from Services.order_manager import order_manager
from model import general_app
from view import Banner, OrderFrame
from Services.watcher_info import watcher_info
import os
import threading
from datetime import datetime, timedelta


AUTO_SAVE_INTERVAL_MIN = 15

def setup_logging():
    log_dir = Path("logs"); log_dir.mkdir(exist_ok=True)
    log_file = log_dir / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log")

    root = logging.getLogger()
    if root.hasHandlers():
        root.handlers.clear()

    handler_file = logging.FileHandler(log_file, encoding='utf-8')
    handler_console = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    for h in (handler_file, handler_console):
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(logging.INFO)
    logging.info("Logging initialised → %s", log_file)


class ArcTriggerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArcTriggerPy")
        self.configure(bg="black")

        # ---------- Banner ----------
        self.banner = Banner(self)
        self.banner.pack(fill="x")

        # ---------- Top control bar ----------
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", pady=10)

        ttk.Button(top_frame, text="Connect", command=self.connect_services).pack(side="left", padx=5)
        ttk.Button(top_frame, text="Disconnect", command=self.disconnect_services).pack(side="left", padx=5)

        ttk.Label(top_frame, text="Order Count:", background="black", foreground="white").pack(side="left", padx=5)
        self.spin_count = tk.Spinbox(top_frame, from_=1, to=10, width=5)
        self.spin_count.pack(side="left", padx=5)
        self.spin_count.delete(0, tk.END)
        self.spin_count.insert(0, "1")

        tk.Button(top_frame, text="Start Trigger", bg="red", fg="white", command=self.build_order_frames).pack(side="left", padx=10)
        tk.Button(top_frame, text="Show Debug", command=self.toggle_debug).pack(side="left", padx=10)
        ttk.Button(top_frame, text="Watchers", command=self.show_watchers).pack(side="left", padx=5)
        # Removed: ttk.Button(top_frame, text="Finalized Orders", ...)

        # ---------- Order container ----------
        self.order_container = ttk.Frame(self)
        self.order_container.pack(fill="both", expand=True)

        self.order_frames = []
        self.debug_frame = None
        self.disconnect_services()
        self.connect_services()
        self.start_conn_monitor()
        self.start_auto_save_thread()

# Attempt auto-restore on startup
        restored = self.load_session(auto=True)
        if restored:
            logging.info("[ArcTriggerApp] Previous session auto-restored.")
        else:
            logging.info("[ArcTriggerApp] No recent session to restore.")

    def save_session(self, filename: str = "arctrigger.dat", background: bool = False):
        """
        Save all visible order frames and their models/orders to arctrigger.dat.
        Includes a timestamp header for auto-restore logic.
        """
        try:
            lines = []
            lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            lines.append(str(len(self.order_frames)))

            for frame in self.order_frames:
                serialized = frame.serialize()
                lines.extend(serialized.split("\n"))

            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            if not background:
                logging.info(f"[ArcTriggerApp.save_session] Saved {len(self.order_frames)} frames → {filename}")
            else:
                logging.info(f"[ArcTriggerApp.auto_save] Background autosave complete.")

        except Exception as e:
            logging.error(f"[ArcTriggerApp.save_session] Failed to save session: {e}")

    
    def load_session(self, filename: str = "arctrigger.dat", auto=False):
        """
        Load frames and their models/orders from arctrigger.dat.
        If auto=True, only loads if file timestamp <= 15 minutes old.
        """
        if not os.path.exists(filename):
            logging.info(f"[ArcTriggerApp.load_session] No session file found.")
            return False

        try:
            with open(filename, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except Exception as e:
            logging.error(f"[ArcTriggerApp.load_session] Failed to read {filename}: {e}")
            return False

        if len(lines) < 2:
            logging.warning("[ArcTriggerApp.load_session] File incomplete.")
            return False

        # Check timestamp
        try:
            timestamp = datetime.strptime(lines[0], "%Y-%m-%d %H:%M:%S")
        except Exception:
            logging.warning("[ArcTriggerApp.load_session] Invalid timestamp header.")
            return False

        if auto:
            if datetime.now() - timestamp > timedelta(minutes=15):
                logging.info("[ArcTriggerApp.load_session] Last session older than 15 minutes, skipping auto-restore.")
                return False

        try:
            count = int(lines[1])
        except ValueError:
            logging.error("[ArcTriggerApp.load_session] Invalid frame count header.")
            return False

        # Clear current frames
        for frame in self.order_frames:
            frame.destroy()
        self.order_frames.clear()

        idx = 2
        loaded = 0
        while idx < len(lines):
            if not lines[idx].startswith("<Frame>|"):
                idx += 1
                continue
            try:
                frame, consumed = OrderFrame.deserialize(lines[idx:], parent=self.order_container)
                frame.pack(fill="x", pady=10, padx=10)
                self.order_frames.append(frame)
                loaded += 1
                idx += consumed
            except Exception as e:
                logging.error(f"[ArcTriggerApp.load_session] Error loading frame at line {idx}: {e}")
                idx += 1

        logging.info(f"[ArcTriggerApp.load_session] Restored {loaded}/{count} frames.")
        return True
    
    def start_auto_save_thread(self):
        """
        Background thread that saves the session every 15 minutes.
        Runs indefinitely until app exits.
        """
        def _loop():
            while True:
                try:
                    self.save_session(background=True)
                except Exception as e:
                    logging.error(f"[ArcTriggerApp.auto_save] Error: {e}")
                threading.Event().wait(AUTO_SAVE_INTERVAL_MIN * 60)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        logging.info(f"[ArcTriggerApp] Auto-save thread started ({AUTO_SAVE_INTERVAL_MIN} min interval).")

    # ------------------------------------------------------------------
    #  CONNECTION MONITOR THREAD
    # ------------------------------------------------------------------
    def start_conn_monitor(self, interval: int = 5):
        import threading
        from Services.tws_service import create_tws_service

        def monitor():
            service = create_tws_service()
            last_state = None
            while True:
                try:
                    current_state = service.conn_status()
                    if current_state != last_state:
                        self.after(0, lambda s=current_state: self.banner.update_connection_status(s))
                        last_state = current_state
                except Exception as e:
                    logging.error(f"[ConnMonitor] Error checking TWS status: {e}")
                finally:
                    time.sleep(interval)

        t = threading.Thread(target=monitor, name="ConnMonitorThread", daemon=True)
        t.start()
        logging.info("Connection monitor thread started.")

    # ------------------------------------------------------------------
    #  WATCHERS WINDOW
    # ------------------------------------------------------------------
    def show_watchers(self):
        import tkinter as tk
        from tkinter import ttk
        win = tk.Toplevel(self)
        win.title("Active Watchers")
        win.geometry("800x300")
        cols = ("Order ID", "Symbol", "Type", "Mode", "Status", "StopLoss", "LastPrice", "StartTime")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=100, anchor="center")
        tree.pack(fill="both", expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for w in watcher_info.list_all():
                tree.insert("", "end", values=(
                    w["order_id"], w["symbol"], w["watcher_type"], w["mode"],
                    w["status_label"], w["stop_loss"], w["last_price"],
                    w["start_time"][:19]))
            win.after(2000, refresh)
        refresh()

    # ------------------------------------------------------------------
    #  CONNECTIONS
    # ------------------------------------------------------------------
    def connect_services(self):
        if general_app.connect():
            self.banner.update_connection_status(True)
            logging.info("Services connected successfully")
        else:
            self.banner.update_connection_status(False)
            logging.error("Failed to connect services")

    def disconnect_services(self):
        general_app.disconnect()
        self.banner.update_connection_status(False)
        logging.info("Services disconnected")

    # ------------------------------------------------------------------
    #  ORDER FRAMES
    # ------------------------------------------------------------------
    def build_order_frames(self):
        """Create order frames based on spinbox value."""
        for frame in self.order_frames:
            frame.destroy()
        self.order_frames.clear()
        try:
            count = int(self.spin_count.get())
        except ValueError:
            count = 1
        for i in range(count):
            frame = OrderFrame(self.order_container, order_id=i + 1)
            frame.pack(fill="x", pady=10, padx=10)
            self.order_frames.append(frame)

    # ------------------------------------------------------------------
    #  DEBUG CONSOLE
    # ------------------------------------------------------------------
    def toggle_debug(self):
        if self.debug_frame and self.debug_frame.winfo_exists():
            self.debug_frame.destroy()
            self.debug_frame = None
            for h in logging.handlers[:]:
                if isinstance(h, TkinterHandler):
                    logging.removeHandler(h)
        else:
            self.debug_frame = DebugFrame(self)
            self.debug_frame.pack(fill="both", expand=True, padx=10, pady=10)
            handler = TkinterHandler(self.debug_frame)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logging.addHandler(handler)
            logging.setLevel(logging.INFO)


# ---------- ENTRY ----------
if __name__ == "__main__":
    setup_logging()
    app = ArcTriggerApp()
    app.mainloop()

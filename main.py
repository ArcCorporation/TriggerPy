import tkinter as tk
from tkinter import ttk
import logging

import time
import logging
from datetime import datetime
from pathlib import Path
from Helpers.debugger import DebugFrame, TkinterHandler
from Services.order_manager import order_manager
from model import general_app
from view import Banner, OrderFrame
from Services.watcher_info import watcher_info

def setup_logging():
    log_dir = Path("logs"); log_dir.mkdir(exist_ok=True)
    log_file = log_dir / (datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log")

    root = logging.getLogger()          # grab root explicitly
    if root.hasHandlers():              # someone else touched it first
        root.handlers.clear()           # burn it down

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
        ttk.Button(top_frame, text="Finalized Orders", command=self.show_finalized_console).pack(side="left", padx=5)

        # ---------- Order container ----------
        self.order_container = ttk.Frame(self)
        self.order_container.pack(fill="both", expand=True)

        self.order_frames = []
        self.debug_frame = None
        self.disconnect_services()
        self.connect_services()
        self.start_conn_monitor()

        # ------------------------------------------------------------------
    #  CONNECTION MONITOR THREAD
    # ------------------------------------------------------------------
    def start_conn_monitor(self, interval: int = 5):
        """
        Launch a background thread that periodically checks the TWS connection
        and updates the banner if status changes.
        """
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
    #  FINALIZED ORDERS CONSOLE  –  BUTTONS INSIDE EACH ROW
    # ------------------------------------------------------------------
    def show_finalized_console(self):
        import tkinter as tk
        from tkinter import ttk
        win = tk.Toplevel(self)
        win.title("Finalized Orders – Manage & Monitor")
        win.geometry("1000x400")

        cols = ("Order ID", "Symbol", "Type", "Qty", "Strike", "Right",
                "Entry $", "Trigger $", "Last $", "P/L %", "Actions")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=90, anchor="center")
        # extra room for the button cluster
        tree.column("#11", width=220, anchor="center")
        tree.pack(fill="both", expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for oid, order in order_manager.finalized_orders.items():
                # 1. insert row (placeholder in Actions)
                iid = tree.insert(
                    "", "end",
                    values=(oid, order.symbol, order.action, order.qty,
                            order.strike, order.right,
                            f"{order.entry_price:.2f}",
                            f"{order.trigger:.2f}",
                            "---", "---", "placeholder")
                )
                # 2. build button frame
                btn_frame = tk.Frame(tree)
                tk.Button(btn_frame, text="BE", width=4,
                          command=lambda o=oid: order_manager.breakeven(o)).pack(side="left", padx=1)
                tk.Button(btn_frame, text="20%", width=4,
                          command=lambda o=oid: order_manager.take_profit(o, 0.20)).pack(side="left", padx=1)
                tk.Button(btn_frame, text="30%", width=4,
                          command=lambda o=oid: order_manager.take_profit(o, 0.30)).pack(side="left", padx=1)
                tk.Button(btn_frame, text="40%", width=4,
                          command=lambda o=oid: order_manager.take_profit(o, 0.40)).pack(side="left", padx=1)
                # 3. slam frame into the cell
                tree.set(iid, column="Actions", value="")
                tree.window_create(iid, column="Actions", window=btn_frame)
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
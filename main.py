import tkinter as tk
from tkinter import ttk
import logging

from Helpers.printer import logger
from Helpers.debugger import DebugFrame, TkinterHandler
from Services.order_manager import order_manager
from model import general_app
from view import Banner, OrderFrame
from Services.watcher_info import watcher_info


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
        ttk.Button(top_frame, text="Finalized Orders", command=self.show_finalized_console).pack(side="left", padx=5)  # <-- NEW

        # ---------- Order container ----------
        self.order_container = ttk.Frame(self)
        self.order_container.pack(fill="both", expand=True)

        self.order_frames = []
        self.debug_frame = None
        self.disconnect_services()
        self.connect_services()

    # ------------------------------------------------------------------
    #  WATCHERS WINDOW (existing)
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
    #  FINALIZED ORDERS CONSOLE (NEW)
    # ------------------------------------------------------------------
    def show_finalized_console(self):
        import tkinter as tk
        from tkinter import ttk
        win = tk.Toplevel(self)
        win.title("Finalized Orders – Manage & Monitor")
        win.geometry("900x400")

        cols = ("Order ID", "Symbol", "Type", "Qty", "Strike", "Right",
                "Entry $", "Trigger $", "Last $", "P/L %", "Actions")
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=90, anchor="center")
        tree.pack(fill="both", expand=True)

        bar = ttk.Frame(win)
        bar.pack(fill="x", pady=5)
        ttk.Button(bar, text="Breakeven", command=lambda: action("breakeven")).pack(side="left", padx=2)
        ttk.Button(bar, text="Take-Profit 20 %", command=lambda: action("tp20")).pack(side="left", padx=2)
        ttk.Button(bar, text="Take-Profit 30 %", command=lambda: action("tp30")).pack(side="left", padx=2)
        ttk.Button(bar, text="Take-Profit 40 %", command=lambda: action("tp40")).pack(side="left", padx=2)

        label_cache = {}   # order_id -> tree iid

        def action(cmd):
            selected = tree.selection()
            if not selected:
                return
            oid = tree.item(selected[0])["values"][0]
            if cmd == "breakeven":
                order_manager.breakeven(oid)
            elif cmd.startswith("tp"):
                pct = int(cmd[2:]) / 100.0
                order_manager.take_profit(oid, pct)
            refresh()

        def refresh():
            tree.delete(*tree.get_children())
            label_cache.clear()
            for oid, order in order_manager.finalized_orders.items():
                row = (
                    oid, order.symbol, order.action, order.qty,
                    order.strike, order.right,
                    f"{order.entry_price:.2f}", f"{order.trigger:.2f}",
                    "---", "---", "Ready"
                )
                iid = tree.insert("", "end", values=row)
                label_cache[oid] = iid
            win.after(1000, refresh)

        refresh()

    # ------------------------------------------------------------------
    #  CONNECTIONS
    # ------------------------------------------------------------------
    def connect_services(self):
        if general_app.connect():
            self.banner.update_connection_status(True)
            logger.info("Services connected successfully")
        else:
            self.banner.update_connection_status(False)
            logger.error("Failed to connect services")

    def disconnect_services(self):
        general_app.disconnect()
        self.banner.update_connection_status(False)
        logger.info("Services disconnected")

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
            self.btn_debug.config(text="Show Debug")
            for h in logger.handlers[:]:
                if isinstance(h, TkinterHandler):
                    logger.removeHandler(h)
        else:
            self.debug_frame = DebugFrame(self)
            self.debug_frame.pack(fill="both", expand=True, padx=10, pady=10)
            self.debug_frame.add_text("[INFO] Debug console started")
            handler = TkinterHandler(self.debug_frame)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            self.btn_debug.config(text="Hide Debug")


# ---------- ENTRY ----------
if __name__ == "__main__":
    app = ArcTriggerApp()
    app.mainloop()
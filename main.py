import tkinter as tk
from tkinter import ttk
import logging

from Helpers.printer import logger
from Helpers.debugger import DebugFrame, TkinterHandler

from model import general_app
from view import Banner, OrderFrame
from Services.watcher_info import watcher_info

class ArcTriggerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArcTriggerPy")
        self.configure(bg="black")

        # Banner
        self.banner = Banner(self)
        self.banner.pack(fill="x")

        # Top control panel
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", pady=10)

        # Connect / Disconnect
        ttk.Button(top_frame, text="Connect", command=self.connect_services).pack(side="left", padx=5)
        ttk.Button(top_frame, text="Disconnect", command=self.disconnect_services).pack(side="left", padx=5)

        # Order frame count + Start
        ttk.Label(top_frame, text="Order Count:", background="black", foreground="white").pack(side="left", padx=5)

        self.spin_count = tk.Spinbox(top_frame, from_=1, to=10, width=5)
        self.spin_count.pack(side="left", padx=5)
        self.spin_count.delete(0, tk.END)
        self.spin_count.insert(0, "1")

        start_btn = tk.Button(top_frame, text="Start Trigger", bg="red", fg="white", command=self.build_order_frames)
        start_btn.pack(side="left", padx=10)

        # Debug toggle
        self.btn_debug = tk.Button(top_frame, text="Show Debug", command=self.toggle_debug)
        self.btn_debug.pack(side="left", padx=10)

        # watchers button
        ttk.Button(top_frame, text="Watchers", command=self.show_watchers).pack(side="left", padx=5)

        # Order frame container
        self.order_container = ttk.Frame(self)
        self.order_container.pack(fill="both", expand=True)

        self.order_frames = []
        self.debug_frame = None
        self.disconnect_services()
        self.connect_services()


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
                tree.insert(
                    "", "end",
                    values=(
                        w["order_id"],
                        w["symbol"],
                        w["watcher_type"],
                        w["mode"],
                        w["status_label"],
                        w["stop_loss"],
                        w["last_price"],
                        w["start_time"][:19],  # trim seconds+ms
                    )
                )
            win.after(2000, refresh)  # auto-refresh every 2s

        refresh()

    # --- Connection handling ---
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

    # --- Order frame handling ---
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

    # --- Debug console toggle ---
    def toggle_debug(self):
        if self.debug_frame and self.debug_frame.winfo_exists():
            # Close debug frame
            self.debug_frame.destroy()
            self.debug_frame = None
            self.btn_debug.config(text="Show Debug")

            for h in logger.handlers[:]:
                if isinstance(h, TkinterHandler):
                    logger.removeHandler(h)

        else:
            # Create and show new debug frame
            self.debug_frame = DebugFrame(self)
            self.debug_frame.pack(fill="both", expand=True, padx=10, pady=10)
            self.debug_frame.add_text("[INFO] Debug console started")

            handler = TkinterHandler(self.debug_frame)
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

            self.btn_debug.config(text="Hide Debug")


if __name__ == "__main__":
    app = ArcTriggerApp()
    app.mainloop()

# view.py
import tkinter as tk
from tkinter import ttk
from model import AppModel


class Banner(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=60, bg="black", highlightthickness=0, **kwargs)
        self.create_text(
            20, 30,
            anchor="w",
            text="ARCTRIGGER",
            font=("Arial Black", 24, "bold"),
            fill="#A020F0"  # mor/pembe ton
        )


class OrderFrame(tk.Frame):
    def __init__(self, parent, model: AppModel, order_id: int = 0, **kwargs):
        super().__init__(parent, relief="groove", borderwidth=2, padx=10, pady=10, bg="black", **kwargs)
        self.model = model
        self.order_id = order_id
        self.price_watcher = None

        # --- Stock + Market Price + Trigger ---
        ttk.Label(self, text="Stock").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.combo_symbol = ttk.Combobox(self, width=12)
        self.combo_symbol.grid(row=0, column=1, padx=5)
        self.combo_symbol.bind("<<ComboboxSelected>>", self.on_symbol_selected)

        self.lbl_price = ttk.Label(self, text="Market Price: -", foreground="white", background="black")
        self.lbl_price.grid(row=0, column=2, padx=10)

        ttk.Label(self, text="Trigger Level").grid(row=0, column=3)
        self.entry_trigger = ttk.Entry(self, width=10)
        self.entry_trigger.grid(row=0, column=4, padx=5)

        self.var_type = tk.StringVar(value="CALL")
        ttk.Radiobutton(self, text="Call", variable=self.var_type, value="CALL").grid(row=0, column=5, padx=2)
        ttk.Radiobutton(self, text="Put", variable=self.var_type, value="PUT").grid(row=0, column=6, padx=2)

        # --- Strike & Maturity ---
        ttk.Label(self, text="Strike").grid(row=1, column=0)
        self.entry_strike = ttk.Entry(self, width=10)
        self.entry_strike.grid(row=1, column=1, padx=5)

        ttk.Label(self, text="Maturity").grid(row=1, column=2)
        self.entry_maturity = ttk.Entry(self, width=12)
        self.entry_maturity.grid(row=1, column=3, padx=5)

        # --- Contract box ---
        self.contract_box = tk.Canvas(self, width=20, height=20, bg="yellow", highlightthickness=1, relief="solid")
        self.contract_box.grid(row=1, column=4, padx=5)

        # --- Order Type ---
        ttk.Label(self, text="Order Type").grid(row=1, column=5)
        self.var_ordertype = tk.StringVar(value="MKT")
        ttk.Radiobutton(self, text="MKT", variable=self.var_ordertype, value="MKT").grid(row=1, column=6)
        ttk.Radiobutton(self, text="LMT", variable=self.var_ordertype, value="LMT").grid(row=1, column=7)

        # --- Offset fields ---
        ttk.Label(self, text="Offset").grid(row=1, column=8)
        self.entry_offset = ttk.Entry(self, width=5)
        self.entry_offset.insert(0, "0.00")
        self.entry_offset.grid(row=1, column=9, padx=2)
        for val in ["0.20", "0.10", "0.05"]:
            ttk.Button(self, text=val, command=lambda v=val: self.entry_offset.delete(0, tk.END) or self.entry_offset.insert(0, v)).grid(row=1, column=10, padx=2)

        # --- Position Size ---
        ttk.Label(self, text="Position Size ($)").grid(row=2, column=0)
        self.entry_possize = ttk.Entry(self, width=10)
        self.entry_possize.insert(0, "0")
        self.entry_possize.grid(row=2, column=1, padx=5)
        for val in ["5K", "10K", "25K"]:
            ttk.Button(self, text=f"${val}", command=lambda v=val: self.set_position_size(v)).grid(row=2, column=2, padx=2)

        # --- Quantity & Stop Loss ---
        ttk.Label(self, text="Quantity").grid(row=2, column=3)
        self.entry_qty = ttk.Entry(self, width=8)
        self.entry_qty.grid(row=2, column=4, padx=5)

        ttk.Label(self, text="Stop Loss ($)").grid(row=2, column=5)
        self.entry_sl = ttk.Entry(self, width=8)
        self.entry_sl.grid(row=2, column=6, padx=5)
        for val in ["0.20", "0.50", "1.00"]:
            ttk.Button(self, text=f"${val}", command=lambda v=val: self.entry_sl.delete(0, tk.END) or self.entry_sl.insert(0, v)).grid(row=2, column=7, padx=2)

        # --- Profit Taking ---
        ttk.Label(self, text="Profit Taking").grid(row=2, column=8)
        self.entry_tp = ttk.Entry(self, width=8)
        self.entry_tp.grid(row=2, column=9, padx=5)
        for val in ["10", "25", "50"]:
            ttk.Button(self, text=f"{val}%", command=lambda v=val: self.entry_tp.delete(0, tk.END) or self.entry_tp.insert(0, v)).grid(row=2, column=10, padx=2)

        ttk.Button(self, text="Take Profit").grid(row=2, column=11, padx=5)

        # --- Control Buttons ---
        frame_ctrl = ttk.Frame(self)
        frame_ctrl.grid(row=3, column=0, columnspan=12, pady=10)
        tk.Button(frame_ctrl, text="Save", bg="green", fg="white").pack(side="left", padx=5)
        tk.Button(frame_ctrl, text="Invalidate", bg="red", fg="white").pack(side="left", padx=5)
        tk.Button(frame_ctrl, text="Breakeven", bg="gray", fg="white").pack(side="left", padx=5)
        tk.Button(frame_ctrl, text="Cancel", bg="darkred", fg="white").pack(side="left", padx=5)

    # --- Helpers ---
    def set_position_size(self, val):
        if val.endswith("K"):
            num = int(val.replace("K", "")) * 1000
            self.entry_possize.delete(0, tk.END)
            self.entry_possize.insert(0, str(num))

    def on_symbol_selected(self, event=None):
        symbol = self.combo_symbol.get()
        if symbol:
            # PolygonService üzerinden fiyat takibi
            self.model.subscribe_price(symbol, self.update_price)

    def update_price(self, price):
        self.lbl_price.config(text=f"Market Price: {price}")


# ---------------- Test Main ----------------
if __name__ == "__main__":
    root = tk.Tk()
    root.title("ArcTriggerPy - Test View")
    model = AppModel()

    banner = Banner(root)
    banner.pack(fill="x")

    frame = OrderFrame(root, model, order_id=1)
    frame.pack(fill="x", pady=10)

    root.mainloop()

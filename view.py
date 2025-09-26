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
            fill="#A020F0"
        )


class OrderFrame(tk.Frame):
    def __init__(self, parent, model: AppModel, order_id: int = 0, **kwargs):
        super().__init__(parent, relief="groove", borderwidth=2, padx=5, pady=5, **kwargs)
        self.model = model
        self.order_id = order_id

        # === Stock + Market Price ===
        ttk.Label(self, text="Stock").grid(row=0, column=0, sticky="w")
        self.combo_symbol = ttk.Combobox(self, width=10)
        self.combo_symbol.grid(row=0, column=1, padx=5)
        self.combo_symbol.bind("<<ComboboxSelected>>", self.on_symbol_selected)

        self.lbl_price = ttk.Label(self, text="Market Price: -")
        self.lbl_price.grid(row=0, column=2, padx=5)

        ttk.Label(self, text="Trigger Level").grid(row=0, column=3)
        self.entry_trigger = ttk.Entry(self, width=7)
        self.entry_trigger.grid(row=0, column=4, padx=5)

        # === Call/Put ===
        self.var_type = tk.StringVar(value="CALL")
        ttk.Radiobutton(self, text="Call", variable=self.var_type, value="CALL").grid(row=0, column=5, padx=2)
        ttk.Radiobutton(self, text="Put", variable=self.var_type, value="PUT").grid(row=0, column=6, padx=2)

        # === Strike / Maturity ===
        ttk.Label(self, text="Strike").grid(row=1, column=0)
        self.entry_strike = ttk.Entry(self, width=10)
        self.entry_strike.grid(row=1, column=1, padx=5)

        ttk.Label(self, text="Maturity").grid(row=1, column=2)
        self.entry_maturity = ttk.Entry(self, width=10)
        self.entry_maturity.grid(row=1, column=3, padx=5)

        # === Contract (renk kutusu) ===
        self.contract_color = tk.Canvas(self, width=20, height=20, bg="yellow")
        self.contract_color.grid(row=0, column=7, padx=5)

        # === Order Type ===
        ttk.Label(self, text="Order Type").grid(row=0, column=8)
        self.var_order_type = tk.StringVar(value="MKT")
        ttk.Radiobutton(self, text="MKT", variable=self.var_order_type, value="MKT").grid(row=0, column=9)
        ttk.Radiobutton(self, text="LMT", variable=self.var_order_type, value="LMT").grid(row=0, column=10)

        # === Offset ===
        ttk.Label(self, text="Offset").grid(row=0, column=11)
        self.entry_offset = ttk.Entry(self, width=5)
        self.entry_offset.insert(0, "0.00")
        self.entry_offset.grid(row=0, column=12, padx=2)

        for i, val in enumerate(["0.20", "0.10", "0.05"]):
            ttk.Button(self, text=val, command=lambda v=val: self.entry_offset.delete(0, tk.END) or self.entry_offset.insert(0, v)).grid(row=0, column=13+i, padx=1)

        # === Position Size ===
        ttk.Label(self, text="Position Size ($)").grid(row=2, column=0, sticky="w")
        self.entry_pos_size = ttk.Entry(self, width=10)
        self.entry_pos_size.insert(0, "0")
        self.entry_pos_size.grid(row=2, column=1, padx=5)

        for i, val in enumerate(["5K", "10K", "25K"]):
            amount = str(int(val.replace("K", "")) * 1000)
            ttk.Button(self, text=f"${val}", command=lambda v=amount: self.set_position_size(v)).grid(row=2, column=2+i, padx=2)

        # === Quantity ===
        ttk.Label(self, text="Quantity").grid(row=2, column=5)
        self.entry_qty = ttk.Entry(self, width=7)
        self.entry_qty.grid(row=2, column=6, padx=5)

        # === Stop Loss ===
        ttk.Label(self, text="Stop Loss ($)").grid(row=2, column=7)
        self.entry_sl = ttk.Entry(self, width=7)
        self.entry_sl.grid(row=2, column=8, padx=5)

        for i, val in enumerate(["0.20", "0.50", "1.00"]):
            ttk.Button(self, text=f"${val}", command=lambda v=val: self.set_stop_loss(v)).grid(row=2, column=9+i, padx=2)

        # === Profit Taking ===
        ttk.Label(self, text="Profit Taking").grid(row=2, column=12)
        self.entry_tp = ttk.Entry(self, width=7)
        self.entry_tp.grid(row=2, column=13, padx=5)

        for i, val in enumerate(["10%", "25%", "50%"]):
            ttk.Button(self, text=val, command=lambda v=val: self.set_profit_taking(v)).grid(row=2, column=14+i, padx=2)

        ttk.Button(self, text="Take Profit", command=self.take_profit).grid(row=2, column=17, padx=5)

        # === Control Buttons ===
        frame_ctrl = ttk.Frame(self)
        frame_ctrl.grid(row=3, column=0, columnspan=18, pady=10)

        ttk.Button(frame_ctrl, text="Save", command=self.save_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Invalidate", command=self.invalidate_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Breakeven", command=self.breakeven_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Cancel", command=self.cancel_order).pack(side="left", padx=5)

    # === Helpers ===
    def set_position_size(self, val):
        self.entry_pos_size.delete(0, tk.END)
        self.entry_pos_size.insert(0, val)

    def set_stop_loss(self, val):
        self.entry_sl.delete(0, tk.END)
        self.entry_sl.insert(0, val)

    def set_profit_taking(self, val):
        self.entry_tp.delete(0, tk.END)
        self.entry_tp.insert(0, val.replace("%", ""))

    def update_price(self, price: float):
        self.lbl_price.config(text=f"Market Price: {price:.2f}")

    def on_symbol_selected(self, event=None):
        sym = self.combo_symbol.get()
        price = self.model.set_symbol(sym)
        if price:
            self.update_price(price)

    def save_order(self):
        order = self.model.place_order(
            action="BUY" if self.var_type.get() == "CALL" else "SELL",
            quantity=int(self.entry_qty.get() or 1),
            trigger=float(self.entry_trigger.get() or 0.0)
        )
        self.update_state(order.state)

    def invalidate_order(self):
        self.model.cancel_order(self.order_id)
        self.update_state("CANCELLED")

    def breakeven_order(self):
        self.model.set_breakeven()
        self.entry_sl.delete(0, tk.END)
        self.entry_sl.insert(0, str(self.model.stop_loss))

    def cancel_order(self):
        self.model.cancel_order(self.order_id)
        self.update_state("CANCELLED")

    def take_profit(self):
        tp = float(self.entry_tp.get() or 0.0)
        self.model.set_take_profit(tp)

    def update_state(self, state: str):
        self.lbl_price.config(text=f"Market Price: {self.lbl_price.cget('text')} | State: {state}")


# ---------------- Test Main ----------------
if __name__ == "__main__":
    root = tk.Tk()
    root.title("ArcTriggerPy - Test View")

    banner = Banner(root)
    banner.pack(fill="x")

    model = AppModel()
    order1 = OrderFrame(root, model, order_id=1)
    order1.pack(fill="x", pady=5)

    order2 = OrderFrame(root, model, order_id=2)
    order2.pack(fill="x", pady=5)

    order1.update_price(423.64)
    order2.update_price(424.23)

    root.mainloop()

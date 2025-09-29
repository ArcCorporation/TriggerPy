# view.py
import tkinter as tk
from tkinter import ttk
from model import AppModel
import logging

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
        super().__init__(parent, relief="groove", borderwidth=2, padx=8, pady=8, **kwargs)
        self.model = model
        self.order_id = order_id

        # ---------------- Stock + Market Price ----------------
        ttk.Label(self, text="Stock").grid(row=0, column=0, sticky="w")
        self.combo_symbol = ttk.Combobox(self, width=22)
        self.combo_symbol.grid(row=0, column=1, padx=5)
        self.combo_symbol.bind("<KeyRelease>", self.on_symbol_typed)
        self.combo_symbol.bind("<<ComboboxSelected>>", self.on_symbol_selected)

        self.lbl_price = ttk.Label(self, text="Market Price: -")
        self.lbl_price.grid(row=0, column=2, padx=5)

        ttk.Label(self, text="Trigger").grid(row=0, column=3)
        self.entry_trigger = ttk.Entry(self, width=8)
        self.entry_trigger.grid(row=0, column=4, padx=5)

        # ---------------- Type (Call/Put) + Order Type ----------------
        self.var_type = tk.StringVar(value="CALL")
        ttk.Radiobutton(self, text="Call", variable=self.var_type, value="CALL").grid(row=0, column=5)
        ttk.Radiobutton(self, text="Put", variable=self.var_type, value="PUT").grid(row=0, column=6)

        ttk.Label(self, text="OrderType").grid(row=0, column=7)
        self.combo_ordertype = ttk.Combobox(self, values=["MKT", "LMT"], width=6, state="readonly")
        self.combo_ordertype.grid(row=0, column=8, padx=5)
        self.combo_ordertype.current(0)

        # ---------------- Strike + Maturity ----------------
        ttk.Label(self, text="Strike").grid(row=1, column=0)
        self.entry_strike = ttk.Entry(self, width=8)
        self.entry_strike.grid(row=1, column=1, padx=5)

        ttk.Label(self, text="Maturity").grid(row=1, column=2)
        self.entry_maturity = ttk.Entry(self, width=10)
        self.entry_maturity.grid(row=1, column=3, padx=5)

        # ---------------- Offset + Position Size ----------------
        ttk.Label(self, text="Offset").grid(row=1, column=4)
        self.entry_offset = ttk.Entry(self, width=8)
        self.entry_offset.grid(row=1, column=5, padx=5)

        ttk.Label(self, text="Pos Size").grid(row=1, column=6)
        self.entry_pos = ttk.Entry(self, width=8)
        self.entry_pos.grid(row=1, column=7, padx=5)

        # ---------------- Quantity + Stop Loss + Profit ----------------
        ttk.Label(self, text="Qty").grid(row=2, column=0)
        self.entry_qty = ttk.Entry(self, width=8)
        self.entry_qty.grid(row=2, column=1, padx=5)

        ttk.Label(self, text="StopLoss").grid(row=2, column=2)
        self.entry_sl = ttk.Entry(self, width=8)
        self.entry_sl.grid(row=2, column=3, padx=5)

        ttk.Label(self, text="Profit %").grid(row=2, column=4)
        self.entry_tp = ttk.Entry(self, width=8)
        self.entry_tp.grid(row=2, column=5, padx=5)

        ttk.Button(self, text="Take Profit", command=self.take_profit).grid(row=2, column=6, padx=5)

        # ---------------- Control Buttons ----------------
        frame_ctrl = ttk.Frame(self)
        frame_ctrl.grid(row=3, column=0, columnspan=9, pady=8)
        ttk.Button(frame_ctrl, text="Save", command=self.save_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Invalidate", command=self.invalidate_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Breakeven", command=self.breakeven_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Cancel", command=self.cancel_order).pack(side="left", padx=5)

    # ---------------- Handlers ----------------
    def on_symbol_typed(self, event):
        query = self.combo_symbol.get().upper()
        if len(query) < 2:
            return
        try:
            results = self.model.tws.search_symbol(query)
            if results:
                symbols = [f"{r['symbol']} - {r.get('primaryExchange', '-')}" for r in results]
                self.combo_symbol["values"] = symbols
        except Exception as e:
            print(f"[UI] Symbol search error: {e}")

    def on_symbol_selected(self, event=None):
        selection = self.combo_symbol.get()
        logging.info(f"[GUI] Symbol selected: {selection}")
        if not selection:
            return

        symbol = selection.split(" - ")[0]
        try:
            snap = self.model.polygon.get_snapshot(symbol)
            if snap and "last" in snap:
                current_price = float(snap["last"])
                self.update_price(current_price)

                # Strike = market price
                self.entry_strike.delete(0, tk.END)
                self.entry_strike.insert(0, f"{current_price:.2f}")

                # Qty = 1
                self.entry_qty.delete(0, tk.END)
                self.entry_qty.insert(0, "1")

                # StopLoss = 0
                self.entry_sl.delete(0, tk.END)
                self.entry_sl.insert(0, "0")

                # Profit % = 0
                self.entry_tp.delete(0, tk.END)
                self.entry_tp.insert(0, "0")

                # Offset = 0
                self.entry_offset.delete(0, tk.END)
                self.entry_offset.insert(0, "0")

                logging.info("[GUI] Auto-fill defaults applied")
        except Exception as e:
            logging.error(f"[UI] Price fetch error: {e}")





    def save_order(self):
        order_data = self.model.place_order(
            action="BUY" if self.var_type.get() == "CALL" else "SELL",
            quantity=int(self.entry_qty.get() or 1),
            trigger=float(self.entry_trigger.get() or 0.0)
        )

        # order_data is a dict (from to_dict), so use key not attribute
        state = order_data.get("state", "").upper()
        self.update_state(state)

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
        print("[UI] Take profit pressed (implement model logic here)")

    # ---------------- Update Methods ----------------
    def update_price(self, value: float):
        self.lbl_price.config(text=f"Market Price: {value:.2f}")

    def update_quantity(self, qty: int):
        self.entry_qty.delete(0, tk.END)
        self.entry_qty.insert(0, str(qty))

    def update_state(self, state: str):
        colors = {"PENDING": "orange", "ACTIVE": "green", "CANCELLED": "red"}
        self.config(highlightbackground=colors.get(state, "gray"), highlightthickness=2)


# ---------------- Test Main ----------------
if __name__ == "__main__":
    root = tk.Tk()
    root.title("ArcTriggerPy - View Test")

    banner = Banner(root)
    banner.pack(fill="x")

    model = AppModel()

    order1 = OrderFrame(root, model, order_id=1)
    order1.pack(fill="x", pady=5)

    order2 = OrderFrame(root, model, order_id=2)
    order2.pack(fill="x", pady=5)

    root.mainloop()

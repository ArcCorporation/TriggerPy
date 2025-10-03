import tkinter as tk
from tkinter import ttk
import logging
import threading
from typing import Optional, Callable

from model import general_app, get_model
from Services import nasdaq_info


# ---------------- Banner ----------------
class Banner(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=60, bg="black", highlightthickness=0, **kwargs)
        self.create_text(20, 30, anchor="w", text="ARCTRIGGER",
                         font=("Arial Black", 24, "bold"), fill="#A020F0")

        self.connection_status = self.create_text(
            400, 30, anchor="w", text="🔴 DISCONNECTED", font=("Arial", 10), fill="red"
        )

        self.market_info = self.create_text(
            600, 30, anchor="w", text="Market info unavailable", font=("Arial", 10), fill="white"
        )

        self.update_market_info()

    def update_connection_status(self, connected: bool):
        status = "🟢 CONNECTED" if connected else "🔴 DISCONNECTED"
        color = "green" if connected else "red"
        self.itemconfig(self.connection_status, text=status, fill=color)

    def update_market_info(self):
        try:
            msg = nasdaq_info.market_status_string()
            self.itemconfig(self.market_info, text=msg, fill="white")
        except Exception as e:
            logging.error(f"Banner market info error: {e}")
        finally:
            self.after(30000, self.update_market_info)


# ---------------- Symbol Selector (threaded search) ----------------
class SymbolSelector(ttk.Frame):
    """Combobox that searches symbols on a worker thread to avoid UI lag."""
    def __init__(self, parent, on_symbol_selected: Callable[[str], None], **kwargs):
        super().__init__(parent, **kwargs)
        self.on_symbol_selected_cb = on_symbol_selected

        ttk.Label(self, text="Stock").pack(side="left")
        self.combo_symbol = ttk.Combobox(self, width=20)
        self.combo_symbol.pack(side="left", padx=5)
        self.combo_symbol.bind("<KeyRelease>", self._on_typed)
        self.combo_symbol.bind("<<ComboboxSelected>>", self._on_selected)

        self.lbl_price = ttk.Label(self, text="Price: -")
        self.lbl_price.pack(side="left", padx=10)
        self.watcher = None
        # threading state
        self._search_req_id = 0
        self._search_lock = threading.Lock()

    # ---- typed search (debounced by latest-wins id) ----
    def _on_typed(self, event=None):
        query = self.combo_symbol.get().upper()
        if len(query) < 2:
            self.combo_symbol["values"] = ()
            return
        with self._search_lock:
            self._search_req_id += 1
            req_id = self._search_req_id
        threading.Thread(target=self._search_worker, args=(query, req_id), daemon=True).start()

    def _search_worker(self, query: str, req_id: int):
        try:
            results = general_app.search_symbol(query)
            if not results:
                values = []
            else:
                values = [f"{r['symbol']} - {r.get('primaryExchange', '-')}" for r in results]
        except Exception as e:
            logging.error(f"Symbol search error: {e}")
            values = []
        # only apply if this is the latest request
        def apply():
            if req_id == self._search_req_id:
                self.combo_symbol["values"] = values
        self.after(0, apply)

    # ---- selection event ----
    def _on_selected(self, event=None):
        selection = self.combo_symbol.get()
        if not selection:
            return
        symbol = selection.split(" - ")[0]
        logging.info(f"Symbol selected: {symbol}")
        
        self.watcher = general_app.watch_price(symbol,self._update_price)
        # fetch price in background to avoid blocking UI
        threading.Thread(target=self._price_worker, args=(symbol,), daemon=True).start()
        self.on_symbol_selected_cb(symbol)
    
    def _update_price(self, price):
        self.lbl_price.config(text=f"Price: {price}")

    def _price_worker(self, symbol: str):
        try:
            snap = general_app.get_snapshot(symbol)
            price_txt = "-"
            if snap and "last" in snap:
                current_price = float(snap["last"])
                price_txt = f"{current_price:.2f}"
        except Exception as e:
            logging.error(f"Price fetch error: {e}")
            price_txt = "-"
        self.after(0, lambda: self.lbl_price.config(text=f"Price: {price_txt}"))


# ---------------- Order Frame (threads for all blocking ops) ----------------
class OrderFrame(tk.Frame):
    def __init__(self, parent, order_id: int = 0, **kwargs):
        super().__init__(parent, relief="groove", borderwidth=2, padx=8, pady=8, **kwargs)
        self.order_id = order_id
        self.model = None
        self.current_symbol: Optional[str] = None
        self._last_price = None   # cached last market price

        # request ids to prevent stale updates
        self._symbol_token = 0
        self._maturity_req_id = 0
        self._strike_req_id = 0

        # --- Symbol selector ---
        symbol_frame = ttk.Frame(self)
        symbol_frame.grid(row=0, column=0, columnspan=9, sticky="ew", pady=5)
        self.symbol_selector = SymbolSelector(symbol_frame, self.on_symbol_selected)
        self.symbol_selector.pack(fill="x")

        # --- Trigger ---
        ttk.Label(self, text="Trigger").grid(row=1, column=0)
        self.entry_trigger = ttk.Entry(self, width=8)
        self.entry_trigger.grid(row=1, column=1, padx=5)

        # --- Type + Order type ---
        self.var_type = tk.StringVar(value="CALL")
        ttk.Radiobutton(self, text="Call", variable=self.var_type, value="CALL").grid(row=1, column=2)
        ttk.Radiobutton(self, text="Put", variable=self.var_type, value="PUT").grid(row=1, column=3)

        ttk.Label(self, text="OrderType").grid(row=1, column=4)
        self.combo_ordertype = ttk.Combobox(self, values=["MKT", "LMT"], width=6, state="readonly")
        self.combo_ordertype.grid(row=1, column=5, padx=5)
        self.combo_ordertype.current(0)

        # --- Position Size ---
        ttk.Label(self, text="Position Size").grid(row=2, column=0)
        self.entry_pos_size = ttk.Entry(self, width=10)
        self.entry_pos_size.grid(row=2, column=1, padx=5)
        self.entry_pos_size.insert(0, "2000")

        # Bind events for manual typing
        self.entry_pos_size.bind("<KeyRelease>", lambda e: self.recalc_quantity())
        self.entry_pos_size.bind("<FocusOut>", lambda e: self.recalc_quantity())

        frame_pos_btns = ttk.Frame(self)
        frame_pos_btns.grid(row=3, column=0, columnspan=2, pady=2)
        for val in [500, 1000, 2000, 5000]:
            ttk.Button(
                frame_pos_btns, text=str(val),
                command=lambda v=val: self._set_pos_and_recalc(v)
            ).pack(side="left", padx=2)

        # --- Maturity ---
        ttk.Label(self, text="Maturity").grid(row=2, column=2)
        self.combo_maturity = ttk.Combobox(self, width=10, state="readonly")
        self.combo_maturity.grid(row=2, column=3, padx=5)
        self.combo_maturity.bind("<<ComboboxSelected>>", self.on_maturity_selected)

        # --- Qty + SL + TP ---
        ttk.Label(self, text="Qty").grid(row=3, column=2)
        self.entry_qty = ttk.Entry(self, width=8)
        self.entry_qty.grid(row=3, column=3, padx=5)
        self.entry_qty.insert(0, "1")

        # --- Stop Loss ---
        ttk.Label(self, text="Stop Loss").grid(row=3, column=4)
        self.entry_sl = ttk.Entry(self, width=8)
        self.entry_sl.grid(row=3, column=5, padx=5)

        frame_sl_btns = ttk.Frame(self)
        frame_sl_btns.grid(row=3, column=6, columnspan=2, padx=5)
        for val in [0.25, 0.50, 1.00, 2.00]:
            ttk.Button(
                frame_sl_btns, text=str(val),
                command=lambda v=val: self._set_stop_loss(v)
            ).pack(side="left", padx=2)

        ttk.Label(self, text="Take Profit").grid(row=3, column=8)
        self.entry_tp = ttk.Entry(self, width=8)
        self.entry_tp.grid(row=3, column=9, padx=5)

        

        # --- Controls ---
        frame_ctrl = ttk.Frame(self)
        frame_ctrl.grid(row=4, column=0, columnspan=9, pady=8)
        self.btn_save = ttk.Button(frame_ctrl, text="Place Order", command=self.place_order, state="disabled")
        self.btn_save.pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Cancel Order", command=self.cancel_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Reset", command=self.reset).pack(side="left", padx=5)

        # --- Status ---
        self.lbl_status = ttk.Label(self, text="Select symbol to start", foreground="gray")
        self.lbl_status.grid(row=5, column=0, columnspan=9, pady=5)
    # ---------- helpers ----------

    def _set_stop_loss(self, value: float):
        """Set Stop Loss entry directly to offset value (no math here)."""
        self.entry_sl.delete(0, tk.END)
        self.entry_sl.insert(0, str(value))


    def _set_pos_and_recalc(self, value: float):
        """Set position size from quick button and recalc quantity."""
        self.entry_pos_size.delete(0, tk.END)
        self.entry_pos_size.insert(0, str(value))
        self.recalc_quantity()

    def recalc_quantity(self):
        """Recalculate Qty from current position size + last price."""
        if not self.model:
            return
        try:
            price = self.model.refresh_market_price()
            pos_size = float(self.entry_pos_size.get() or 2000)
            qty = self.model.calculate_quantity(pos_size, price)
            self.entry_qty.delete(0, tk.END)
            self.entry_qty.insert(0, str(qty))
        except Exception as e:
            logging.error(f"Quantity recalc error: {e}")
    def _ui(self, fn, *args, **kwargs):
        """Thread-safe UI update."""
        self.after(0, lambda: fn(*args, **kwargs))

    def _set_status(self, text: str, color: str = "gray"):
        self._ui(self.lbl_status.config, text=text, foreground=color)

    # ---------- events ----------
    def on_symbol_selected(self, symbol: str):
        self.current_symbol = symbol
        self.model = get_model(symbol)
        self._symbol_token += 1
        token = self._symbol_token

        self._set_status(f"Ready: {symbol}", "blue")
        self.btn_save.config(state="normal")

        # price + prefill trigger + quantity off-thread
        def price_worker():
            try:
                price = self.model.refresh_market_price()
            except Exception as e:
                logging.error(f"Auto-fill price error: {e}")
                price = None
            def apply():
                if token != self._symbol_token:
                    return
                if price:
                    # trigger
                    trigger_price = price + 0.10 if self.var_type.get() == "CALL" else price - 0.10
                    self.entry_trigger.delete(0, tk.END)
                    self.entry_trigger.insert(0, f"{trigger_price:.2f}")
                    # quantity from position size
                    try:
                        pos_size = float(self.entry_pos_size.get() or 2000)
                        qty = self.model.calculate_quantity(pos_size, price)
                        self.entry_qty.delete(0, tk.END)
                        self.entry_qty.insert(0, str(qty))
                    except Exception as e:
                        logging.error(f"Quantity calc error: {e}")
            self._ui(apply)
        threading.Thread(target=price_worker, daemon=True).start()

        # maturities off-thread (model handles any internal waits)
        self.load_maturities_async(token)

    def on_maturity_selected(self, event=None):
        maturity = self.combo_maturity.get()
        if maturity and self.model:
            self.load_strikes_async(maturity)

    # ---------- async loaders ----------
    def load_maturities_async(self, token: int):
        self._maturity_req_id += 1
        req_id = self._maturity_req_id

        def worker():
            try:
                maturities = self.model.get_available_maturities()
            except Exception as e:
                logging.error(f"Maturity load error: {e}")
                maturities = []
            def apply():
                if token != self._symbol_token or req_id != self._maturity_req_id:
                    return  # stale
                self.combo_maturity["values"] = maturities
                if maturities:
                    self.combo_maturity.set(maturities[0])
            self._ui(apply)
        threading.Thread(target=worker, daemon=True).start()

    def load_strikes_async(self, maturity: str):
        # placeholder if needed later
        pass

    # ---------- actions ----------
    def place_order(self):
        if not self.model or not self.current_symbol:
            self._set_status("Error: No symbol selected", "red")
            return

        try:
            position_size = float(self.entry_pos_size.get() or 2000)
            maturity = self.combo_maturity.get()
            right = self.var_type.get()
            quantity = int(self.entry_qty.get() or 1)
            trigger_str = self.entry_trigger.get()
            trigger = float(trigger_str) if trigger_str else None
            sl = float(self.entry_sl.get()) if self.entry_sl.get() else None
            tp = float(self.entry_tp.get()) if self.entry_tp.get() else None
        except Exception as e:
            self._set_status(f"Order input error: {e}", "red")
            return

        self.btn_save.config(state="disabled")
        self._set_status("Placing order...", "orange")

        def worker():
            try:
                strike = round(self._last_price)
                self.model.set_option_contract(maturity, strike, right)
                if sl is not None:
                    self.model._stop_loss = sl
                if tp is not None:
                    self.model._take_profit = tp

                order_data = self.model.place_option_order(
                    action="BUY", position_size=position_size, quantity=quantity, trigger_price=trigger
                )
                state = order_data.get("state", "UNKNOWN")
                msg = f"Order {state}: {order_data.get('order_id')}"
                color = "green" if state == "ACTIVE" else "orange"
                def ok():
                    self._set_status(msg, color)
                    self.btn_save.config(state="normal")
                self._ui(ok)
                logging.info(f"Order placed: {order_data}")
            except Exception as e:
                def err():
                    self._set_status(f"Order failed: {e}", "red")
                    self.btn_save.config(state="normal")
                self._ui(err)
                logging.error(f"Order failed: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def cancel_order(self):
        if not self.model:
            return
        def worker():
            try:
                pending = self.model.get_orders("PENDING")
                for order in pending:
                    self.model.cancel_pending_order(order["order_id"])
                self._ui(lambda: self._set_status("Pending orders cancelled", "orange"))
            except Exception as e:
                logging.error(f"Cancel error: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def reset(self):
        self._symbol_token += 1
        self.model = None
        self.current_symbol = None

        self.symbol_selector.combo_symbol.set('')
        self.symbol_selector.lbl_price.config(text="Price: -")
        self.entry_trigger.delete(0, tk.END)
        self.entry_pos_size.delete(0, tk.END)
        self.entry_pos_size.insert(0, "2000")
        self.combo_maturity.set('')
        self.entry_qty.delete(0, tk.END)
        self.entry_qty.insert(0, "1")
        self.entry_sl.delete(0, tk.END)
        self.entry_tp.delete(0, tk.END)
        self._set_status("Select symbol to start", "gray")
        self.btn_save.config(state="disabled")

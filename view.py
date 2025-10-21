import tkinter as tk
from tkinter import ttk
import logging
import threading
from typing import Optional, Callable

from model import general_app, get_model, AppModel
from Services import nasdaq_info
from Services.order_manager import order_manager
from Helpers.Order import OrderState


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


# ------------------------------------------------------------------
#  SymbolSelector  –  NASDAQ / NYSE only  +  Shift-↑/Tab auto-pick
# ------------------------------------------------------------------
class SymbolSelector(ttk.Frame):
    """Combobox that searches symbols on a worker thread to avoid UI lag."""
    def __init__(self, parent, on_symbol_selected: Callable[[str], None], **kwargs):
        super().__init__(parent, **kwargs)
        self.on_symbol_selected_cb = on_symbol_selected

        ttk.Label(self, text="Stock").pack(side="left")
        self.combo_symbol = ttk.Combobox(self, width=20)
        self.combo_symbol.pack(side="left", padx=5)

        # NEW: accelerate keys
        self.combo_symbol.bind("<KeyRelease>", self._on_typed)
        self.combo_symbol.bind("<<ComboboxSelected>>", self._on_selected)
        self.combo_symbol.bind("<Shift-Up>", self._auto_select_first)
        self.combo_symbol.bind("<Tab>", self._auto_select_first)

        self.lbl_price = ttk.Label(self, text="Price: -")
        self.lbl_price.pack(side="left", padx=10)
        self.watcher = None

        self._search_req_id = 0
        self._search_lock = threading.Lock()

    # ----------------------------------------------------------
    #  1.  Filtered search worker  (NASDAQ / NYSE only)
    # ----------------------------------------------------------
    def _search_worker(self, query: str, req_id: int):
        try:
            raw = general_app.search_symbol(query) or []
            filtered = [
                r for r in raw
                if (r.get("primaryExchange") or "").upper() in {"NASDAQ", "NYSE"}
            ]
            values = [f"{r['symbol']} - {r['primaryExchange']}" for r in filtered]
        except Exception as e:
            logging.error(f"Symbol search error: {e}")
            values = []

        def apply():
            if req_id == self._search_req_id:
                self.combo_symbol["values"] = values
        self.after(0, apply)

    # ----------------------------------------------------------
    #  2.  Auto-select first hit on Shift-↑ or Tab
    # ----------------------------------------------------------
    def _auto_select_first(self, event=None):
        vals = self.combo_symbol["values"]
        if vals:
            self.combo_symbol.set(vals[0])
            self._on_selected()
        return "break"

    # ----------------------------------------------------------
    #  3.  Original typed search (unchanged logic)
    # ----------------------------------------------------------
    def _on_typed(self, event=None):
        query = self.combo_symbol.get().upper()
        if len(query) < 2:
            self.combo_symbol["values"] = ()
            return
        with self._search_lock:
            self._search_req_id += 1
            req_id = self._search_req_id
        threading.Thread(target=self._search_worker, args=(query, req_id), daemon=True).start()

    # ----------------------------------------------------------
    #  4.  Selection handler (unchanged)
    # ----------------------------------------------------------
    def _on_selected(self, event=None):
        selection = self.combo_symbol.get()
        if not selection:
            return
        symbol = selection.split(" - ")[0]
        logging.info(f"Symbol selected: {symbol}")

        self.watcher = general_app.watch_price(symbol, self._update_price)
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
        self.is_finalized = False  # track if this frame's order finalized

        # --- Symbol selector ---
        symbol_frame = ttk.Frame(self)
        symbol_frame.grid(row=0, column=0, columnspan=9, sticky="ew", pady=5)
        self.symbol_selector = SymbolSelector(symbol_frame, self.on_symbol_selected)
        self.symbol_selector.pack(fill="x")

        # --- Trigger ---
        ttk.Label(self, text="Trigger").grid(row=1, column=0)
        self.entry_trigger = ttk.Entry(self, width=8)
        self.entry_trigger.grid(row=1, column=1, padx=5)
        self.entry_trigger.bind('<Up>',   lambda e: self._bump_trigger(+0.50))
        self.entry_trigger.bind('<Down>', lambda e: self._bump_trigger(-0.50))
        self.entry_trigger.bind('<Shift-Up>',   lambda e: self._bump_trigger(+0.10))
        self.entry_trigger.bind('<Shift-Down>', lambda e: self._bump_trigger(-0.10))

        # --- Type + Order type ---
        self.var_type = tk.StringVar(value="CALL")
        ttk.Radiobutton(self, text="Call", variable=self.var_type, value="CALL").grid(row=1, column=2)
        ttk.Radiobutton(self, text="Put", variable=self.var_type, value="PUT").grid(row=1, column=3)
        # When CALL/PUT changes, repopulate strikes if price known
        self.var_type.trace_add("write", self._on_type_changed)
        self.var_use_25 = tk.BooleanVar(value=True)   # default ON
        chk_25 = ttk.Checkbutton(self, text="Use 2.5-step",
                                variable=self.var_use_25,
                                command=self._on_type_changed)
        chk_25.grid(row=1, column=4, padx=6, sticky="w")

        # --- Order Type (fixed LMT) ---
        ttk.Label(self, text="Type").grid(row=1, column=4)
        ttk.Label(self, text="LMT", foreground="gray").grid(row=1, column=5)

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
        ttk.Label(self, text="Strike").grid(row=2, column=4)
        self.combo_strike = ttk.Combobox(self, width=8, state="disabled")
        self.combo_strike.grid(row=2, column=5, padx=5)

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
        self.frame_actions = ttk.Frame(self)
        self.frame_actions.grid(row=6, column=0, columnspan=9, pady=5)
        #self.frame_actions.grid_remove()

        self.btn_be = ttk.Button(self.frame_actions, text="Breakeven",
                                command=self._on_breakeven, state="disabled")
        self.btn_be.pack(side="left", padx=3)

        self.tp_buttons = []
        for pct in (20, 30, 40):
            btn = ttk.Button(self.frame_actions, text=f"TP {pct}%",
                            command=lambda p=pct: self._on_take_profit(p),
                            state="disabled")
            btn.pack(side="left", padx=3)
            self.tp_buttons.append(btn)

        # --- Offset row  (was aggressive checkbox) -------------------------
        offset_frame = ttk.Frame(self)
        offset_frame.grid(row=5, column=0, columnspan=9, pady=8)

        ttk.Label(offset_frame, text="Offset").pack(side="left", padx=(0, 4))
        self.entry_offset = ttk.Entry(offset_frame, width=6)
        self.entry_offset.pack(side="left")
        self.entry_offset.insert(0, "0.01")          # sane default

        for val in (0.01, 0.05, 0.15):
            ttk.Button(
                offset_frame, text=f"{val:.2f}",
                command=lambda v=val: self._set_offset(v),
                width=4
            ).pack(side="left", padx=2)
        self.btn_save = ttk.Button(frame_ctrl, text="Place Order", command=self.place_order, state="disabled")
        self.btn_save.pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Cancel Order", command=self.cancel_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Reset", command=self.reset).pack(side="left", padx=5)

        # --- Status ---
        self.lbl_status = ttk.Label(self, text="Select symbol to start", foreground="gray")
        self.lbl_status.grid(row=7, column=0, columnspan=9, pady=5)

    # ---------- helpers ----------

    def _on_type_changed(self, *args):
        """Repopulate strikes when CALL/PUT toggled."""
        try:
            if not self.model:
                return
            price = self.model.refresh_market_price()
            if price:
                self._populate_strike_combo(price)
        except Exception as e:
            logging.error(f"Type change repopulate error: {e}")


    def _populate_strike_combo(self, centre: float):
        step = 2.5 if self.var_use_25.get() else 1.0
        count = 5
        strikes = []

        if self.var_type.get() == "CALL":
            first = self._next_tick(centre, step, up=True)
            strikes = [first + i * step for i in range(count)]
        else:  # PUT
            last = self._next_tick(centre, step, up=False)
            strikes = [last - i * step for i in range(count)]
            strikes.reverse()   # keep descending order in combo

        # plain integers when step==1, else 1-decimal for 2.5
        fmt = "{:.0f}" if step == 1.0 else "{:.1f}"
        self.combo_strike["values"] = [fmt.format(v) for v in strikes]

        # default to closest to centre
        best = min(strikes, key=lambda v: abs(v - centre))
        self.combo_strike.set(fmt.format(best))
        self.combo_strike.config(state="readonly")

# helper: snap to next valid tick (up or down)
    def _next_tick(self, price: float, step: float, up: bool) -> float:
        base = int(price / step) * step
        if up and base <= price:
            base += step
        elif not up and base >= price:
            base -= step
        return base
    def _set_offset(self, value: float):
        """Slam the offset entry with the pressed button's value."""
        self.entry_offset.delete(0, tk.END)
        self.entry_offset.insert(0, f"{value:.2f}")
    # ---------- helper ----------
    def _bump_trigger(self, delta: float):
        try:
            current = float(self.entry_trigger.get() or 0)
        except ValueError:
            current = 0.0

        new = current + delta

        # snap to nearest 0.50 only for big ($0.50) steps
        if abs(delta) >= 0.5:
            new = round(new * 2) / 2

        self.entry_trigger.delete(0, tk.END)
        self.entry_trigger.insert(0, f"{new:.2f}")
        return "break"
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
        try:
            if self.model and self.model.order:
                order = self.model.order
                if order.state == OrderState.FINALIZED:
                    self._ui(self._on_order_finalized)
        except Exception as e:
            logging.error(f"[OrderFrame] Finalization check error: {e}")

    # ---------- events ----------
    def on_symbol_selected(self, symbol: str):
        self.current_symbol = symbol
        self.model = get_model(symbol)
        self._symbol_token += 1
        token = self._symbol_token
        self.model.set_status_callback(self._set_status)
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
                    self._populate_strike_combo(price) 
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
                all_maturities = self.model.get_available_maturities()   # raw strings YYYYMMDD
                # keep only the first 4 chronologically
                kept = sorted(all_maturities)[:4]
            except Exception as e:
                logging.error(f"Maturity load error: {e}")
                kept = []

            def apply():
                if token != self._symbol_token or req_id != self._maturity_req_id:
                    return  # stale
                self.combo_maturity["values"] = kept
                if kept:
                    self.combo_maturity.set(kept[0])
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
            position_size = float(self.entry_pos_size.get() or 50000)
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
                strike = float(self.combo_strike.get())
                
                
                self.model.set_option_contract(maturity, strike, right)
                if sl is not None:
                    self.model._stop_loss = sl
                if tp is not None:
                    self.model._take_profit = tp
                # new
                try:
                    arcTick = float(self.entry_offset.get() or 1.06)
                except ValueError:
                    arcTick = 1.06
                order_data = self.model.place_option_order(
                    action="BUY", quantity=quantity, trigger_price=trigger,
                    position=position_size,
                    arcTick=arcTick
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
                self._e = e
                def err():
                    
                    self._set_status(f"Order failed: {self._e}", "red")
                    self.btn_save.config(state="normal")
                self._ui(err)
                logging.error(f"Order failed: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def cancel_order(self):
        if not self.model:
            return
        def worker():
            try:
                pending = self.model.order
                
                self.model.cancel_pending_order(pending.order_id)
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

    def _on_order_finalized(self):
        """Enable manual actions when backend finalizes order"""
        if not self.is_finalized:
            self.is_finalized = True
            self.frame_actions.grid()
            self.btn_be.config(state="normal")
            for btn in self.tp_buttons:
                btn.config(state="normal")
            self._set_status("Order Finalized – controls enabled", "green")

    def _on_breakeven(self):
        def worker():
            try:
                if self.model and self.model.order:
                    order = self.model.order
                    order_manager.breakeven(order.order_id)
                    self._ui(lambda: self._set_status("Breakeven triggered", "blue"))
                else:
                    self._ui(lambda: self._set_status("Error: No active order", "red"))
            except Exception as e:
                logging.error(f"Breakeven error: {e}")
                self._ui(lambda: self._set_status(f"Error: {e}", "red"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_take_profit(self, pct):
        def worker():
            try:
                if self.model and self.model.order:
                    order = self.model.order
                    order_manager.take_profit(order.order_id, pct / 100)
                    self._ui(lambda: self._set_status(f"Take Profit {pct}% triggered", "blue"))
                else:
                    logging.error("Take-Profit error: No active order")
                    self._ui(lambda: self._set_status("Error: No active order", "red"))
            except Exception as e:
                logging.error(f"Take-Profit error: {e}")
                self._ui(lambda: self._set_status(f"Error: {e}", "red"))

        threading.Thread(target=worker, daemon=True).start()

    
    def serialize(self) -> str:
        """
        Serialize this frame's UI metadata and its associated model.
        The frame never inspects or manipulates model.order directly.

        Layer delimiters:
            Frame   → '|'
            Model   → ':'
            Order   → '_'

        Output Example:
            <Frame>|id=1|state=ACTIVE|symbol=AAPL
            AppModel:18dc9329:AAPL:20251018:195.0:C:2.1:2.7:True
            Order:18dc9329_AAPL_20251018_195.0_C_1_2.35_2.5_2.0_BUY_195.1_PENDING_None
        """
        frame_id = getattr(self, "frame_id", id(self))
        state = getattr(self, "_view_state", "UNKNOWN")
        symbol = getattr(self.model, "symbol", "None") if hasattr(self, "model") else "None"

        header = f"<Frame>|id={frame_id}|state={state}|symbol={symbol}"

        model = getattr(self, "model", None)
        if model is not None:
            model_block = model.serialize()
            return f"{header}\n{model_block}"
        return header

    @classmethod
    def deserialize(cls, lines: list[str], parent) -> tuple["OrderFrame", int]:
        """
        Deserialize a frame block and its attached model.

        Expected input:
            <Frame>|id=<id>|state=<state>|symbol=<symbol>
            AppModel:...
            [Order:...]

        Returns:
            (OrderFrame instance, lines_consumed)
        """
        if not lines or not lines[0].startswith("<Frame>|"):
            raise ValueError("Expected a line starting with '<Frame>|'")

        # --- Parse frame header ---
        parts = lines[0].split("|")
        frame_id = None
        state = "UNKNOWN"
        symbol = "None"

        for p in parts[1:]:
            if p.startswith("id="):
                frame_id = p.split("=", 1)[1]
            elif p.startswith("state="):
                state = p.split("=", 1)[1]
            elif p.startswith("symbol="):
                symbol = p.split("=", 1)[1]

        # --- Instantiate frame ---
        frame = cls(parent)
        frame.frame_id = frame_id
        frame._view_state = state

        # --- Restore symbol correctly ---
        try:
            if symbol and symbol != "None":
                # Set symbol in combobox instead of non-existent symbol_var
                frame.symbol_selector.combo_symbol.set(symbol)
                # Optionally rebuild the model so buttons etc. get enabled
                frame.on_symbol_selected(symbol)
                logging.info(f"[OrderFrame.deserialize] Restored symbol {symbol}")
        except Exception as e:
            logging.error(f"[OrderFrame.deserialize] Symbol restore failed: {e}")

        consumed = 1

        # --- Parse attached model if present ---
        if len(lines) > 1 and lines[1].startswith("AppModel:"):
            try:
                model, used = AppModel.deserialize(lines[1:3])
                frame.model = model
                consumed += used
            except Exception as e:
                logging.error(f"[OrderFrame.deserialize] Model restore failed: {e}")

        return frame, consumed


# view.py
import tkinter as tk
from tkinter import ttk
import logging
from typing import Optional, Callable

from model import general_app, get_model, AppModel


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
        
        # Connection status
        self.connection_status = self.create_text(
            400, 30,
            anchor="w", 
            text="🔴 DISCONNECTED",
            font=("Arial", 10),
            fill="red"
        )

    def update_connection_status(self, connected: bool):
        status = "🟢 CONNECTED" if connected else "🔴 DISCONNECTED"
        color = "green" if connected else "red"
        self.itemconfig(self.connection_status, text=status, fill=color)


class SymbolSelector(ttk.Frame):
    """Reusable symbol selection component"""
    def __init__(self, parent, on_symbol_selected: Callable[[str], None], **kwargs):
        super().__init__(parent, **kwargs)
        self.on_symbol_selected = on_symbol_selected
        
        ttk.Label(self, text="Stock").pack(side="left")
        self.combo_symbol = ttk.Combobox(self, width=20)
        self.combo_symbol.pack(side="left", padx=5)
        self.combo_symbol.bind("<KeyRelease>", self.on_symbol_typed)
        self.combo_symbol.bind("<<ComboboxSelected>>", self.on_combobox_selected)
        
        self.lbl_price = ttk.Label(self, text="Price: -")
        self.lbl_price.pack(side="left", padx=10)

    def on_symbol_typed(self, event):
        query = self.combo_symbol.get().upper()
        if len(query) < 2:
            return
        try:
            results = general_app.search_symbol(query)
            if results:
                symbols = [f"{r['symbol']} - {r.get('primaryExchange', '-')}" for r in results]
                self.combo_symbol["values"] = symbols
        except Exception as e:
            logging.error(f"Symbol search error: {e}")

    def on_combobox_selected(self, event=None):
        selection = self.combo_symbol.get()
        if not selection:
            return
        
        symbol = selection.split(" - ")[0]
        logging.info(f"Symbol selected: {symbol}")
        
        # Get market data
        try:
            snap = general_app.get_snapshot(symbol)
            if snap and "last" in snap:
                current_price = float(snap["last"])
                self.lbl_price.config(text=f"Price: {current_price:.2f}")
        except Exception as e:
            logging.error(f"Price fetch error: {e}")
        
        # Notify parent
        self.on_symbol_selected(symbol)


class OrderFrame(tk.Frame):
    def __init__(self, parent, order_id: int = 0, **kwargs):
        super().__init__(parent, relief="groove", borderwidth=2, padx=8, pady=8, **kwargs)
        self.order_id = order_id
        self.model: Optional[AppModel] = None
        self.current_symbol: Optional[str] = None

        # ---------------- Symbol Selection ----------------
        symbol_frame = ttk.Frame(self)
        symbol_frame.grid(row=0, column=0, columnspan=9, sticky="ew", pady=5)
        
        self.symbol_selector = SymbolSelector(symbol_frame, self.on_symbol_selected)
        self.symbol_selector.pack(fill="x")

        # ---------------- Trigger ----------------  
        ttk.Label(self, text="Trigger").grid(row=1, column=0)
        self.entry_trigger = ttk.Entry(self, width=8)
        self.entry_trigger.grid(row=1, column=1, padx=5)

        # ---------------- Type (Call/Put) + Order Type ----------------
        self.var_type = tk.StringVar(value="CALL")
        ttk.Radiobutton(self, text="Call", variable=self.var_type, value="CALL").grid(row=1, column=2)
        ttk.Radiobutton(self, text="Put", variable=self.var_type, value="PUT").grid(row=1, column=3)

        ttk.Label(self, text="OrderType").grid(row=1, column=4)
        self.combo_ordertype = ttk.Combobox(self, values=["MKT", "LMT"], width=6, state="readonly")
        self.combo_ordertype.grid(row=1, column=5, padx=5)
        self.combo_ordertype.current(0)

        # ---------------- Strike + Maturity ----------------
        ttk.Label(self, text="Strike").grid(row=2, column=0)
        self.entry_strike = ttk.Entry(self, width=8)
        self.entry_strike.grid(row=2, column=1, padx=5)

        ttk.Label(self, text="Maturity").grid(row=2, column=2)
        self.combo_maturity = ttk.Combobox(self, width=10, state="readonly")
        self.combo_maturity.grid(row=2, column=3, padx=5)
        self.combo_maturity.bind("<<ComboboxSelected>>", self.on_maturity_selected)

        # ---------------- Strike Selection ----------------
        ttk.Label(self, text="Select Strike").grid(row=2, column=4)
        self.combo_strike = ttk.Combobox(self, width=8, state="readonly")
        self.combo_strike.grid(row=2, column=5, padx=5)

        # ---------------- Quantity + Stop Loss + Take Profit ----------------
        ttk.Label(self, text="Qty").grid(row=3, column=0)
        self.entry_qty = ttk.Entry(self, width=8)
        self.entry_qty.grid(row=3, column=1, padx=5)
        self.entry_qty.insert(0, "1")

        ttk.Label(self, text="Stop Loss").grid(row=3, column=2)
        self.entry_sl = ttk.Entry(self, width=8)
        self.entry_sl.grid(row=3, column=3, padx=5)

        ttk.Label(self, text="Take Profit").grid(row=3, column=4)
        self.entry_tp = ttk.Entry(self, width=8)
        self.entry_tp.grid(row=3, column=5, padx=5)

        # ---------------- Control Buttons ----------------
        frame_ctrl = ttk.Frame(self)
        frame_ctrl.grid(row=4, column=0, columnspan=9, pady=8)
        
        self.btn_save = ttk.Button(frame_ctrl, text="Place Order", command=self.place_order, state="disabled")
        self.btn_save.pack(side="left", padx=5)
        
        ttk.Button(frame_ctrl, text="Cancel Order", command=self.cancel_order).pack(side="left", padx=5)
        ttk.Button(frame_ctrl, text="Reset", command=self.reset).pack(side="left", padx=5)

        # ---------------- Status ----------------
        self.lbl_status = ttk.Label(self, text="Select symbol to start", foreground="gray")
        self.lbl_status.grid(row=5, column=0, columnspan=9, pady=5)

    def on_symbol_selected(self, symbol: str):
        """When a symbol is selected in the symbol selector"""
        self.current_symbol = symbol
        self.model = get_model(symbol)
        
        # Update status
        self.lbl_status.config(text=f"Ready: {symbol}", foreground="blue")
        self.btn_save.config(state="normal")
        
        # Load maturities
        self.load_maturities()
        
        # Auto-fill trigger based on current price
        try:
            current_price = self.model.refresh_market_price()
            if current_price:
                trigger_price = current_price + 0.10 if self.var_type.get() == "CALL" else current_price - 0.10
                self.entry_trigger.delete(0, tk.END)
                self.entry_trigger.insert(0, f"{trigger_price:.2f}")
                
                # Auto-fill nearest strike
                self.entry_strike.delete(0, tk.END)
                self.entry_strike.insert(0, f"{current_price:.2f}")
        except Exception as e:
            logging.error(f"Auto-fill error: {e}")

    def load_maturities(self):
        """Load available maturities for current symbol"""
        if not self.model:
            return
            
        try:
            maturities = self.model.get_available_maturities()
            self.combo_maturity["values"] = maturities
            if maturities:
                self.combo_maturity.set(maturities[0])
                self.load_strikes(maturities[0])
        except Exception as e:
            logging.error(f"Maturity load error: {e}")

    def on_maturity_selected(self, event=None):
        """When maturity is selected, load strikes"""
        maturity = self.combo_maturity.get()
        if maturity and self.model:
            self.load_strikes(maturity)

    def load_strikes(self, maturity: str):
        """Load available strikes for selected maturity"""
        try:
            strikes = self.model.get_available_strikes(maturity)
            strike_values = [str(s) for s in strikes]
            self.combo_strike["values"] = strike_values
            if strike_values and self.entry_strike.get():
                # Auto-select nearest strike to entered value
                try:
                    current_strike = float(self.entry_strike.get())
                    nearest = min(strikes, key=lambda s: abs(s - current_strike))
                    self.combo_strike.set(str(nearest))
                except ValueError:
                    pass
        except Exception as e:
            logging.error(f"Strike load error: {e}")

    def place_order(self):
        """Place option order using the new model"""
        if not self.model or not self.current_symbol:
            self.lbl_status.config(text="Error: No symbol selected", foreground="red")
            return

        try:
            # Get strike from combo or entry
            strike_str = self.combo_strike.get() or self.entry_strike.get()
            if not strike_str:
                raise ValueError("Strike price required")
                
            strike = float(strike_str)
            maturity = self.combo_maturity.get()
            right = self.var_type.get()
            quantity = int(self.entry_qty.get() or 1)
            
            trigger_str = self.entry_trigger.get()
            trigger = float(trigger_str) if trigger_str else None

            # Set contract
            self.model.set_option_contract(maturity, strike, right)
            
            # Set risk management
            if self.entry_sl.get():
                self.model.set_stop_loss(float(self.entry_sl.get()))
            if self.entry_tp.get():
                self.model.set_take_profit(float(self.entry_tp.get()))

            # Place order
            order_data = self.model.place_option_order(
                action="BUY",  # Could make this configurable
                quantity=quantity,
                trigger_price=trigger
            )

            # Update UI
            state = order_data.get("state", "UNKNOWN")
            self.lbl_status.config(text=f"Order {state}: {order_data.get('order_id')}", 
                                 foreground="green" if state == "ACTIVE" else "orange")
            
            logging.info(f"Order placed: {order_data}")

        except Exception as e:
            error_msg = f"Order failed: {str(e)}"
            self.lbl_status.config(text=error_msg, foreground="red")
            logging.error(error_msg)

    def cancel_order(self):
        """Cancel all pending orders for this symbol"""
        if self.model:
            try:
                # Cancel all pending orders
                pending_orders = self.model.get_orders("PENDING")
                for order in pending_orders:
                    self.model.cancel_pending_order(order["order_id"])
                
                self.lbl_status.config(text="Pending orders cancelled", foreground="orange")
            except Exception as e:
                logging.error(f"Cancel error: {e}")

    def reset(self):
        """Reset this order frame"""
        self.model = None
        self.current_symbol = None
        self.symbol_selector.combo_symbol.set('')
        self.symbol_selector.lbl_price.config(text="Price: -")
        self.entry_trigger.delete(0, tk.END)
        self.entry_strike.delete(0, tk.END)
        self.combo_maturity.set('')
        self.combo_strike.set('')
        self.entry_qty.delete(0, tk.END)
        self.entry_qty.insert(0, "1")
        self.entry_sl.delete(0, tk.END)
        self.entry_tp.delete(0, tk.END)
        self.lbl_status.config(text="Select symbol to start", foreground="gray")
        self.btn_save.config(state="disabled")


# ---------------- Updated Main Window ----------------
class ArcTriggerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArcTriggerPy - Multi-Symbol")
        self.geometry("1200x800")

        # Banner with connection status
        self.banner = Banner(self)
        self.banner.pack(fill="x")
        
        # Connection control
        conn_frame = ttk.Frame(self)
        conn_frame.pack(fill="x", pady=5)
        
        ttk.Button(conn_frame, text="Connect", command=self.connect_services).pack(side="left", padx=5)
        ttk.Button(conn_frame, text="Disconnect", command=self.disconnect_services).pack(side="left", padx=5)
        
        # Order frame management
        control_frame = ttk.Frame(self)
        control_frame.pack(fill="x", pady=5)
        
        ttk.Label(control_frame, text="Order Frames:").pack(side="left", padx=5)
        self.spin_count = tk.Spinbox(control_frame, from_=1, to=10, width=5)
        self.spin_count.pack(side="left", padx=5)
        self.spin_count.delete(0, tk.END)
        self.spin_count.insert(0, "1")
        
        ttk.Button(control_frame, text="Create Frames", command=self.build_order_frames).pack(side="left", padx=5)
        ttk.Button(control_frame, text="Clear All", command=self.clear_all_frames).pack(side="left", padx=5)

        # Order frames container
        self.order_container = ttk.Frame(self)
        self.order_container.pack(fill="both", expand=True)
        
        self.order_frames = []

    def connect_services(self):
        if general_app.connect():
            self.banner.update_connection_status(True)
            logging.info("Services connected successfully")

    def disconnect_services(self):
        general_app.disconnect()
        self.banner.update_connection_status(False)
        logging.info("Services disconnected")

    def build_order_frames(self):
        """Create multiple independent order frames"""
        self.clear_all_frames()
        
        try:
            count = int(self.spin_count.get())
        except ValueError:
            count = 1
            
        for i in range(count):
            frame = OrderFrame(self.order_container, order_id=i + 1)
            frame.pack(fill="x", padx=10, pady=5)
            self.order_frames.append(frame)

    def clear_all_frames(self):
        """Clear all order frames"""
        for frame in self.order_frames:
            frame.destroy()
        self.order_frames.clear()


if __name__ == "__main__":
    app = ArcTriggerApp()
    app.mainloop()
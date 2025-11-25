import tkinter as tk
from tkinter import ttk
import logging
import time
from Services.options_manager import options_manager


REFRESH_MS = 1000   # 1 second UI refresh


def open_positions_window(parent):
    win = tk.Toplevel(parent)
    win.title("ArcTrigger — Position Monitor")
    win.geometry("1100x500")

    columns = (
        "UUID", "Symbol", "Qty", "Avg", "Mid", "PnL",
        "Delta", "Gamma", "Theta", "Vega",
        "Exposure", "Status", "Stale", "Action"
    )

    tree = ttk.Treeview(win, columns=columns, show="headings", height=20)
    tree.pack(fill="both", expand=True)

    # Setup column sizes
    tree.column("UUID", width=120)
    tree.column("Symbol", width=80)
    tree.column("Qty", width=50)
    tree.column("Avg", width=70)
    tree.column("Mid", width=70)
    tree.column("PnL", width=90)
    tree.column("Delta", width=70)
    tree.column("Gamma", width=70)
    tree.column("Theta", width=70)
    tree.column("Vega", width=70)
    tree.column("Exposure", width=100)
    tree.column("Status", width=70)
    tree.column("Stale", width=50)
    tree.column("Action", width=70)

    for c in columns:
        tree.heading(c, text=c)

    # Scrollbars
    scroll_y = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll_y.set)
    scroll_y.pack(side="right", fill="y")

    # Close position handler
    def on_close(uuid):
        try:
            options_manager.close_position(uuid)
            logging.info(f"[opmng_ui] Requested close for {uuid}")
        except Exception as e:
            logging.error(f"[opmng_ui] Close failed for {uuid}: {e}")

    # Handle click on “Action”
    def on_tree_click(event):
        item = tree.identify_row(event.y)
        col = tree.identify_column(event.x)

        if col == f"#{len(columns)}":
            vals = tree.item(item)["values"]
            if vals:
                uuid = vals[0]
                on_close(uuid)

    tree.bind("<Button-1>", on_tree_click)

    # Periodic refresh
    def refresh():
        for r in tree.get_children():
            tree.delete(r)

        try:
            positions = options_manager.list_positions()
        except Exception as e:
            logging.error(f"[opmng_ui] Failed to fetch positions: {e}")
            win.after(REFRESH_MS, refresh)
            return

        for pos in positions:
            pnl = pos["unrealized_pnl"]
            pnl_str = f"{pnl:.2f}" if pnl is not None else "-"

            tree.insert("", "end", values=(
                pos["uuid"],
                pos["symbol"],
                pos["qty"],
                round(pos["avg_price"], 4) if pos["avg_price"] else "-",
                round(pos["mid"], 4) if pos["mid"] else "-",
                pnl_str,
                round(pos["delta"], 4) if pos["delta"] else "-",
                round(pos["gamma"], 4) if pos["gamma"] else "-",
                round(pos["theta"], 4) if pos["theta"] else "-",
                round(pos["vega"], 4) if pos["vega"] else "-",
                round(pos["exposure"], 2) if pos["exposure"] else "-",
                pos["status"],
                "YES" if pos["stale"] else "NO",
                "Close"
            ))

        win.after(REFRESH_MS, refresh)

    refresh()

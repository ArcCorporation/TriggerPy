# work_symbols_view.py

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import logging

from model import general_app
from Services.work_symbols import WorkSymbols
from Services.persistent_conid_storage import storage, PersistentConidStorage


class WorkSymbolsView(tk.Toplevel):
    def __init__(self, parent, storage: PersistentConidStorage = storage):
        super().__init__(parent)
        self.title("Work Symbols (Daily)")
        self.geometry("700x500")

        self.storage = storage
        self.work_symbols = WorkSymbols(self.storage)

        self._build_ui()
        self._refresh_list()

    # -------------------------------------------------
    # UI
    # -------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Search Symbol").pack(side="left")
        self.entry_search = ttk.Entry(top, width=20)
        self.entry_search.pack(side="left", padx=5)
        self.entry_search.bind("<KeyRelease>", self._on_search)

        self.combo_results = ttk.Combobox(top, width=25)
        self.combo_results.pack(side="left", padx=5)

        ttk.Button(top, text="Add", command=self._add_symbol).pack(side="left", padx=5)

        # -------------------------------------------------
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ("Symbol", "ConID Ready")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, anchor="center")

        self.tree.pack(fill="both", expand=True)

        # -------------------------------------------------
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=10)

        ttk.Button(bottom, text="Remove Selected", command=self._remove_selected).pack(side="left")
        ttk.Button(bottom, text="Refresh ConIDs", command=self._refresh_conids).pack(side="left", padx=10)
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="right")

    # -------------------------------------------------
    # Logic
    # -------------------------------------------------
    def _on_search(self, event=None):
        query = self.entry_search.get().upper()
        if len(query) < 2:
            self.combo_results["values"] = ()
            return

        def worker():
            try:
                results = general_app.search_symbol(query) or []
                values = [
                    r["symbol"]
                    for r in results
                    if (r.get("primaryExchange") or "").upper() in {"NASDAQ", "NYSE"}
                ]
            except Exception as e:
                logging.error(f"Search error: {e}")
                values = []

            self.after(0, lambda: self.combo_results.configure(values=values))

        threading.Thread(target=worker, daemon=True).start()

    def _add_symbol(self):
        symbol = self.combo_results.get().strip().upper()
        if not symbol:
            return
        self.work_symbols.add_symbol(symbol)
        self._refresh_list()

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        symbol = self.tree.item(sel[0])["values"][0]
        self.work_symbols.remove_symbol(symbol)
        self._refresh_list()

    def _refresh_conids(self):
        if not general_app.is_connected:
            messagebox.showerror("Error", "TWS not connected")
            return

        def worker():
            self.work_symbols.refresh_all_conids()
            self.after(0, self._refresh_list)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for symbol, ready in self.work_symbols.get_ready_symbols().items():
            self.tree.insert("", "end", values=(symbol, "✅" if ready else "❌"))

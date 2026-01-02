# work_symbols.py

from typing import Dict
from persistent_conid_storage import storage, PersistentConidStorage


class WorkSymbols:
    """
    Maintains an in-memory map of symbols -> bool
    True  = a conid exists in DB for this symbol
    False = no conid stored (or symbol known but unresolved)
    """

    def __init__(self, storage: PersistentConidStorage = storage):
        self.storage = storage
        self.symbols: Dict[str, bool] = {}

    def add_symbol(self, symbol: str) -> None:
        """
        Add a symbol to tracking.
        Initial value is determined by DB state.
        """
        symbol = symbol.upper()
        self.symbols[symbol] = self.storage.get_conid(symbol) is not None

    def remove_symbol(self, symbol: str) -> None:
        """
        Remove a symbol from tracking (does NOT delete DB data).
        """
        symbol = symbol.upper()
        self.symbols.pop(symbol, None)

    def has_symbol(self, symbol: str) -> bool:
        """
        Check if symbol is tracked.
        """
        return symbol.upper() in self.symbols

    def check(self) -> None:
        """
        Re-check DB for all tracked symbols and update internal state.
        """
        for symbol in list(self.symbols.keys()):
            self.symbols[symbol] = self.storage.get_conid(symbol) is not None

    def get_ready_symbols(self) -> Dict[str, bool]:
        """
        Return a copy of the internal symbol map.
        """
        return dict(self.symbols)

    def unresolved_symbols(self) -> Dict[str, bool]:
        """
        Return symbols without conids.
        """
        return {s: v for s, v in self.symbols.items() if not v}

    def resolved_symbols(self) -> Dict[str, bool]:
        """
        Return symbols with conids.
        """
        return {s: v for s, v in self.symbols.items() if v}

work_symbols = WorkSymbols()
# Example usage:
# storage = PersistentConidStorage()
# ws = WorkSymbols(storage)
# ws.add_symbol("AAPL")
# ws.add_symbol("TSLA")
# ws.check()
# print(ws.get_ready_symbols())

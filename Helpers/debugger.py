import tkinter as tk
from tkinter import scrolledtext
import logging


class DebugFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.text = scrolledtext.ScrolledText(self, wrap="word", state="disabled")
        self.text.pack(fill="both", expand=True)

    def add_text(self, msg: str):
        """Append a line of text to the debug console."""
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n")
        self.text.configure(state="disabled")
        self.text.see("end")


class TkinterHandler(logging.Handler):
    """Custom logging handler that forwards log messages into DebugFrame."""
    def __init__(self, debug_frame: DebugFrame):
        super().__init__()
        self.debug_frame = debug_frame

    def emit(self, record):
        msg = self.format(record)
        self.debug_frame.add_text(msg)

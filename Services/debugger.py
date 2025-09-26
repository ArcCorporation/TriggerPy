# debugger.py
import tkinter as tk
from tkinter import scrolledtext


class DebugFrame(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        # Text alanı + scrollbar
        self.text_area = scrolledtext.ScrolledText(
            self,
            wrap=tk.WORD,
            height=12,
            width=80,
            state="disabled",   # sadece yazdırma için
            bg="black",
            fg="lime",
            font=("Consolas", 10)
        )
        self.text_area.pack(fill="both", expand=True)

    def add_text(self, message: str):
        """Yeni satır ekler ve otomatik scroll yapar."""
        self.text_area.configure(state="normal")
        self.text_area.insert(tk.END, message + "\n")
        self.text_area.configure(state="disabled")
        self.text_area.see(tk.END)


# --- Test main ---
if __name__ == "__main__":
    root = tk.Tk()
    root.title("ArcTriggerPy - Debug Console")

    dbg = DebugFrame(root)
    dbg.pack(fill="both", expand=True)

    dbg.add_text("[INFO] Debug console started")
    dbg.add_text("[OK] System initialized")
    dbg.add_text("[ERROR] Dummy error log here")

    root.mainloop()

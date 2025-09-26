import tkinter as tk
from tkinter import ttk
from model import AppModel
from view import Banner, OrderFrame


class ArcTriggerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ArcTriggerPy")
        self.configure(bg="black")

        # Model (tek instance)
        self.model = AppModel()

        # Banner
        banner = Banner(self)
        banner.pack(fill="x")

        # Üst kontrol paneli (Order Count + Start Trigger)
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", pady=10)

        ttk.Label(top_frame, text="Order Count:", background="black", foreground="white").pack(side="left", padx=5)

        self.spin_count = tk.Spinbox(top_frame, from_=1, to=10, width=5)
        self.spin_count.pack(side="left", padx=5)
        self.spin_count.delete(0, tk.END)
        self.spin_count.insert(0, "1")

        start_btn = tk.Button(top_frame, text="Start Trigger", bg="red", fg="white", command=self.build_order_frames)
        start_btn.pack(side="left", padx=10)

        # Order frame container
        self.order_container = ttk.Frame(self)
        self.order_container.pack(fill="both", expand=True)

        # Frame referansları
        self.order_frames = []

    def build_order_frames(self):
        """Spinbox değerine göre order frame’leri oluştur."""
        # Öncekileri temizle
        for frame in self.order_frames:
            frame.destroy()
        self.order_frames.clear()

        try:
            count = int(self.spin_count.get())
        except ValueError:
            count = 1

        # Yeni frame’ler oluştur
        for i in range(count):
            frame = OrderFrame(self.order_container, self.model, order_id=i + 1)
            frame.pack(fill="x", pady=10, padx=10)
            self.order_frames.append(frame)


if __name__ == "__main__":
    app = ArcTriggerApp()
    app.mainloop()

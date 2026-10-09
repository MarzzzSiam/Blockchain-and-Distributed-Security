"""
P2P Network Application
CSE 433 - Blockchain & Distributed Security Lab
University of Asia Pacific

Run:
    python main.py
"""

import queue
import socket
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from p2p_node import P2PNode


class P2PApp:
    def __init__(self, root):
        self.root = root
        self.root.title("P2P Network - TCP Communication")
        self.root.geometry("1000x700")
        self.root.minsize(900, 620)

        self.node = None
        self.events = queue.Queue()
        self.peer_ids = []

        self._build_ui()
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # ---------------- My Peer ----------------
        mine = ttk.LabelFrame(self.root, text="My Peer", padding=10)
        mine.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(mine, text="Peer Name:").grid(row=0, column=0, sticky="w")
        self.name_entry = ttk.Entry(mine, width=22)
        self.name_entry.grid(row=0, column=1, padx=5)

        ttk.Label(mine, text="Listening Port:").grid(row=0, column=2, sticky="w", padx=(20, 0))
        self.port_entry = ttk.Entry(mine, width=10)
        self.port_entry.insert(0, "5000")
        self.port_entry.grid(row=0, column=3, padx=5)

        self.start_btn = ttk.Button(mine, text="Start Peer", command=self.start_peer)
        self.start_btn.grid(row=0, column=4, padx=5)

        self.stop_btn = ttk.Button(mine, text="Stop", command=self.stop_peer, state="disabled")
        self.stop_btn.grid(row=0, column=5, padx=5)

        self.my_ip_label = ttk.Label(mine, text="LAN IP: -")
        self.my_ip_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # ---------------- Connect ----------------
        connect = ttk.LabelFrame(self.root, text="Connect to Another Peer", padding=10)
        connect.pack(fill="x", padx=10, pady=5)

        ttk.Label(connect, text="Remote IP:").grid(row=0, column=0, sticky="w")
        self.ip_entry = ttk.Entry(connect, width=22)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=0, column=1, padx=5)

        ttk.Label(connect, text="Remote Port:").grid(row=0, column=2, sticky="w", padx=(20, 0))
        self.remote_port_entry = ttk.Entry(connect, width=10)
        self.remote_port_entry.insert(0, "5001")
        self.remote_port_entry.grid(row=0, column=3, padx=5)

        self.connect_btn = ttk.Button(connect, text="Connect", command=self.connect_to_peer)
        self.connect_btn.grid(row=0, column=4, padx=5)

        ttk.Label(
            connect,
            text="For another computer on the same Wi-Fi/LAN, enter that computer's IPv4 address.",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        # ---------------- Main area ----------------
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        peers_frame = ttk.LabelFrame(main, text="Connected Peers", padding=8)
        peers_frame.pack(side="left", fill="y", padx=(0, 5))

        self.peer_list = tk.Listbox(peers_frame, width=28, height=25)
        self.peer_list.pack(fill="both", expand=True)

        center = ttk.Frame(main)
        center.pack(side="left", fill="both", expand=True, padx=(5, 0))

        log_frame = ttk.LabelFrame(center, text="Messages / Events", padding=8)
        log_frame.pack(fill="both", expand=True)

        self.log = scrolledtext.ScrolledText(
            log_frame, wrap="word", state="disabled", height=25
        )
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("connected", foreground="#16803c")
        self.log.tag_configure("error", foreground="#d32f2f")

        # ---------------- Send text ----------------
        send_frame = ttk.LabelFrame(self.root, text="Send Text", padding=10)
        send_frame.pack(fill="x", padx=10, pady=5)

        self.message_entry = ttk.Entry(send_frame)
        self.message_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.message_entry.bind("<Return>", lambda event: self.send_text())

        self.send_btn = ttk.Button(send_frame, text="Send", command=self.send_text)
        self.send_btn.pack(side="left")

        self.file_btn = ttk.Button(
            send_frame, text="Choose File & Send", command=self.send_file
        )
        self.file_btn.pack(side="left", padx=(5, 0))

        self.status_var = tk.StringVar(value="Status: Not started")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom"
        )

    # ---------------- Utility ----------------

    def log_message(self, message):
        self.log.configure(state="normal")
        lower = message.lower()
        if "error" in lower or "failed" in lower or "refused" in lower or "timed out" in lower:
            tag = "error"
        elif "connected" in lower or "connection established" in lower:
            tag = "connected"
        else:
            tag = None
        self.log.insert("end", message + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                event_type = event[0]

                if event_type == "log":
                    self.log_message(event[1])

                elif event_type == "status":
                    self.status_var.set("Status: " + event[1])

                elif event_type == "peers":
                    self.peer_ids = event[1]
                    self.peer_list.delete(0, "end")
                    for peer in self.peer_ids:
                        self.peer_list.insert(
                            "end",
                            f"{peer['name']} [{peer['id'][:8]}]  {peer['ip']}:{peer['port']}",
                        )

                elif event_type == "local_ip":
                    self.my_ip_label.config(text=f"LAN IP: {event[1]}")

        except queue.Empty:
            pass

        self.root.after(100, self._process_events)

    def _selected_peer_id(self):
        selection = self.peer_list.curselection()
        if not selection:
            messagebox.showwarning("No Peer Selected", "Please select a connected peer first.")
            return None

        return self.peer_ids[selection[0]]["id"]

    # ---------------- Peer lifecycle ----------------

    def start_peer(self):
        if self.node is not None:
            return

        name = self.name_entry.get().strip()
        port_text = self.port_entry.get().strip()

        if not name:
            messagebox.showerror("Invalid Input", "Enter a peer name.")
            return

        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Port must be an integer from 1 to 65535.")
            return

        try:
            self.node = P2PNode(
                peer_name=name,
                listen_port=port,
                event_callback=lambda event: self.events.put(event),
            )
            self.node.start()

            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.status_var.set(f"Status: Running on port {port}")
            self.log_message(f"[START] Peer '{name}' is listening on TCP port {port}.")
            self.log_message(
                "[INFO] Same-LAN peers should connect using this computer's LAN IPv4 address."
            )

        except Exception as exc:
            self.node = None
            messagebox.showerror("Startup Error", str(exc))

    def stop_peer(self):
        if self.node:
            self.node.stop()
            self.node = None

        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("Status: Not started")
        self.peer_ids = []
        self.peer_list.delete(0, "end")
        self.log_message("[STOP] Peer stopped.")

    def connect_to_peer(self):
        if not self.node:
            messagebox.showwarning("Peer Not Started", "Start your peer first.")
            return

        ip = self.ip_entry.get().strip()
        port_text = self.remote_port_entry.get().strip()

        if not ip:
            messagebox.showerror("Invalid Input", "Enter the remote IP address.")
            return

        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Remote port must be 1-65535.")
            return

        self.node.connect_to_peer(ip, port)

    # ---------------- Communication ----------------

    def send_text(self):
        if not self.node:
            messagebox.showwarning("Peer Not Started", "Start your peer first.")
            return

        peer_id = self._selected_peer_id()
        if not peer_id:
            return

        message = self.message_entry.get().strip()
        if not message:
            return

        success = self.node.send_text(peer_id, message)
        if success:
            self.message_entry.delete(0, "end")

    def send_file(self):
        if not self.node:
            messagebox.showwarning("Peer Not Started", "Start your peer first.")
            return

        peer_id = self._selected_peer_id()
        if not peer_id:
            return

        path = filedialog.askopenfilename(title="Select a file to send")
        if not path:
            return

        self.node.send_file(peer_id, path)

    def _on_close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = P2PApp(root)
    root.mainloop()

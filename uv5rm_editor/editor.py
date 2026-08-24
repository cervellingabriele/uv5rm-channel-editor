"""Tkinter desktop front-end for editing Baofeng ``.xlc`` codeplugs.

Run with ``python -m uv5rm_editor`` or ``python uv5rm_editor/editor.py``.
All codeplug logic lives in :mod:`uv5rm_editor.xlc_format`; this file is only
the user interface.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:  # allow running both as a package and as a plain script
    from .xlc_format import (
        CTCSS_TONES,
        NAME_MAXLEN,
        N_ZONES,
        SLOTS_PER_ZONE,
        Channel,
        Codeplug,
        CodeplugError,
        format_frequency,
        format_tone,
    )
except ImportError:  # pragma: no cover - fallback for direct execution
    from xlc_format import (  # type: ignore
        CTCSS_TONES,
        NAME_MAXLEN,
        N_ZONES,
        SLOTS_PER_ZONE,
        Channel,
        Codeplug,
        CodeplugError,
        format_frequency,
        format_tone,
    )

APP_TITLE = "UV-5RM Plus GPS - Fast Channel Editor"


def _zone_choices(zone_names: Dict[int, str]) -> List[str]:
    return [
        f"{z} - {zone_names.get(z, '') or '(unnamed)'}"
        for z in range(1, N_ZONES + 1)
    ]


class ChannelDialog(tk.Toplevel):
    """Modal dialog to create or edit a single channel."""

    def __init__(self, master, zone_names, channel=None, default_zone=1):
        super().__init__(master)
        self.title("Edit channel" if channel else "New channel")
        self.resizable(False, False)
        self.result: Optional[dict] = None
        self.grab_set()

        pad = dict(padx=8, pady=4)
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")

        ttk.Label(frame, text="Name (max 10):").grid(row=0, column=0, sticky="e", **pad)
        self.e_name = ttk.Entry(frame, width=16)
        self.e_name.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Zone:").grid(row=1, column=0, sticky="e", **pad)
        self.cb_zone = ttk.Combobox(frame, width=28, state="readonly",
                                    values=_zone_choices(zone_names))
        self.cb_zone.grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(frame, text="RX (MHz):").grid(row=2, column=0, sticky="e", **pad)
        self.e_rx = ttk.Entry(frame, width=16)
        self.e_rx.grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(frame, text="TX (MHz):").grid(row=3, column=0, sticky="e", **pad)
        self.e_tx = ttk.Entry(frame, width=16)
        self.e_tx.grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(frame, text="(leave empty for simplex)").grid(row=3, column=2, sticky="w")

        ttk.Label(frame, text="TX tone (Hz):").grid(row=4, column=0, sticky="e", **pad)
        self.cb_tone = ttk.Combobox(
            frame, width=14,
            values=["-"] + [f"{t:.1f}" for t in CTCSS_TONES if t],
        )
        self.cb_tone.grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Power:").grid(row=5, column=0, sticky="e", **pad)
        self.cb_power = ttk.Combobox(frame, width=14, state="readonly", values=["High", "Low"])
        self.cb_power.grid(row=5, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Bandwidth:").grid(row=6, column=0, sticky="e", **pad)
        self.cb_band = ttk.Combobox(frame, width=14, state="readonly",
                                    values=["Wide (25k)", "Narrow (12.5k)"])
        self.cb_band.grid(row=6, column=1, sticky="w", **pad)

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(buttons, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        if channel is not None:
            self.e_name.insert(0, channel.name)
            self.cb_zone.current(channel.zone - 1)
            self.e_rx.insert(0, format_frequency(channel.rx))
            self.e_tx.insert(0, format_frequency(channel.tx))
            self.cb_tone.set(format_tone(channel.tone))
            self.cb_power.set("High" if channel.high_power else "Low")
            self.cb_band.set("Wide (25k)" if channel.wide else "Narrow (12.5k)")
        else:
            self.cb_zone.current(default_zone - 1)
            self.cb_tone.set("-")
            self.cb_power.set("High")
            self.cb_band.set("Wide (25k)")

        self.e_name.focus_set()
        self.bind("<Return>", lambda _e: self._accept())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _accept(self) -> None:
        name = self.e_name.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "Name cannot be empty.", parent=self)
            return
        if len(name) > NAME_MAXLEN:
            if not messagebox.askyesno(
                APP_TITLE,
                f"Name will be truncated to {NAME_MAXLEN} characters:\n"
                f"'{name[:NAME_MAXLEN]}'\nContinue?",
                parent=self,
            ):
                return
            name = name[:NAME_MAXLEN]

        try:
            rx = float(self.e_rx.get().replace(",", "."))
            tx_text = self.e_tx.get().replace(",", ".").strip()
            tx = float(tx_text) if tx_text else rx
        except ValueError:
            messagebox.showwarning(APP_TITLE, "Invalid frequency.", parent=self)
            return

        tone_text = self.cb_tone.get().strip()
        tone = 0.0 if tone_text in ("", "-") else float(tone_text.replace(",", "."))

        for value, label in ((rx, "RX"), (tx, "TX")):
            if not 18.0 <= value <= 1000.0:
                messagebox.showwarning(APP_TITLE, f"{label} out of plausible range.", parent=self)
                return
            in_band = (108 <= value <= 174) or (400 <= value <= 520)
            if not in_band and not messagebox.askyesno(
                APP_TITLE,
                f"{label} = {format_frequency(value)} MHz is outside the radio's "
                "typical bands (108-174 / 400-520). Add it anyway?",
                parent=self,
            ):
                return

        self.result = dict(
            name=name,
            zone=self.cb_zone.current() + 1,
            rx=rx,
            tx=tx,
            tone=tone,
            high_power=self.cb_power.get() == "High",
            wide=self.cb_band.get().startswith("Wide"),
        )
        self.destroy()


class BlockDialog(tk.Toplevel):
    """Modal dialog to generate a numbered block of evenly-spaced channels."""

    def __init__(self, master, zone_names, default_zone=1):
        super().__init__(master)
        self.title("Generate channel block")
        self.resizable(False, False)
        self.result: Optional[List[Channel]] = None
        self.grab_set()

        pad = dict(padx=8, pady=4)
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")

        rows = [
            ("Name prefix:", "e_prefix", "LPD"),
            ("Start number:", "e_start", "1"),
            ("Digits (zero pad):", "e_pad", "2"),
            ("Start freq (MHz):", "e_f0", "433.075"),
            ("Step (kHz):", "e_step", "25"),
            ("Count:", "e_count", "10"),
        ]
        for i, (label, attr, default) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", **pad)
            entry = ttk.Entry(frame, width=16)
            entry.insert(0, default)
            entry.grid(row=i, column=1, sticky="w", **pad)
            setattr(self, attr, entry)

        base_row = len(rows)
        ttk.Label(frame, text="Zone:").grid(row=base_row, column=0, sticky="e", **pad)
        self.cb_zone = ttk.Combobox(frame, width=28, state="readonly",
                                    values=_zone_choices(zone_names))
        self.cb_zone.current(default_zone - 1)
        self.cb_zone.grid(row=base_row, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Power:").grid(row=base_row + 1, column=0, sticky="e", **pad)
        self.cb_power = ttk.Combobox(frame, width=14, state="readonly", values=["High", "Low"])
        self.cb_power.set("Low")
        self.cb_power.grid(row=base_row + 1, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Bandwidth:").grid(row=base_row + 2, column=0, sticky="e", **pad)
        self.cb_band = ttk.Combobox(frame, width=14, state="readonly",
                                    values=["Wide (25k)", "Narrow (12.5k)"])
        self.cb_band.set("Narrow (12.5k)")
        self.cb_band.grid(row=base_row + 2, column=1, sticky="w", **pad)

        buttons = ttk.Frame(frame)
        buttons.grid(row=base_row + 3, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text="Generate", command=self._accept).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _accept(self) -> None:
        try:
            prefix = self.e_prefix.get().strip()
            start = int(self.e_start.get())
            pad = int(self.e_pad.get())
            f0 = float(self.e_f0.get().replace(",", "."))
            step = float(self.e_step.get().replace(",", ".")) / 1000.0
            count = int(self.e_count.get())
        except ValueError:
            messagebox.showwarning(APP_TITLE, "Invalid values.", parent=self)
            return
        if not 1 <= count <= SLOTS_PER_ZONE:
            messagebox.showwarning(APP_TITLE, f"Count must be 1..{SLOTS_PER_ZONE}.", parent=self)
            return

        zone = self.cb_zone.current() + 1
        high = self.cb_power.get() == "High"
        wide = self.cb_band.get().startswith("Wide")
        channels = []
        for i in range(count):
            name = f"{prefix}{start + i:0{pad}d}"[:NAME_MAXLEN]
            freq = round(f0 + i * step, 5)
            channels.append(Channel(zone, name, freq, freq, 0.0, high, wide, raw=None, dirty=True))
        self.result = channels
        self.destroy()


class ZoneNamesDialog(tk.Toplevel):
    """Modal dialog to rename all zones at once."""

    def __init__(self, master, zone_names):
        super().__init__(master)
        self.title("Zone names")
        self.resizable(False, False)
        self.result: Optional[Dict[int, str]] = None
        self.grab_set()

        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        self.entries: Dict[int, ttk.Entry] = {}
        for zone in range(1, N_ZONES + 1):
            ttk.Label(frame, text=f"Zone {zone}:").grid(row=zone - 1, column=0, sticky="e",
                                                        padx=6, pady=3)
            entry = ttk.Entry(frame, width=20)
            entry.insert(0, zone_names.get(zone, ""))
            entry.grid(row=zone - 1, column=1, padx=6, pady=3)
            self.entries[zone] = entry

        buttons = ttk.Frame(frame)
        buttons.grid(row=N_ZONES, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _accept(self) -> None:
        self.result = {z: self.entries[z].get().strip()[:16] for z in range(1, N_ZONES + 1)}
        self.destroy()


class EditorApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x600")
        self.minsize(820, 480)
        self.codeplug: Optional[Codeplug] = None
        self.airband_var = tk.BooleanVar(value=False)
        self._build_menu()
        self._build_toolbar()
        self._build_table()
        self._build_status()
        self._refresh()

    # -- UI construction ---------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open .xlc...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_file)
        file_menu.add_command(label="Save as...", command=self.save_file_as)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="New channel...", command=self.add_channel)
        tools_menu.add_command(label="Generate block...", command=self.generate_block)
        tools_menu.add_command(label="Rename zones...", command=self.rename_zones)
        tools_menu.add_separator()
        tools_menu.add_checkbutton(label="Allow aircraft band (108 MHz)",
                                   variable=self.airband_var)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About / Warnings", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)
        self.bind_all("<Control-o>", lambda _e: self.open_file())
        self.bind_all("<Control-s>", lambda _e: self.save_file())

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="Open", command=self.open_file).pack(side="left", padx=3)
        ttk.Button(bar, text="Save", command=self.save_file).pack(side="left", padx=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="New", command=self.add_channel).pack(side="left", padx=3)
        ttk.Button(bar, text="Edit", command=self.edit_channel).pack(side="left", padx=3)
        ttk.Button(bar, text="Duplicate", command=self.duplicate_channel).pack(side="left", padx=3)
        ttk.Button(bar, text="Delete", command=self.delete_channel).pack(side="left", padx=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Generate block", command=self.generate_block).pack(side="left", padx=3)
        ttk.Button(bar, text="Rename zones", command=self.rename_zones).pack(side="left", padx=3)

    def _build_table(self) -> None:
        wrapper = ttk.Frame(self)
        wrapper.pack(side="top", fill="both", expand=True, padx=8, pady=4)
        columns = ("zone", "name", "rx", "tx", "tone", "power", "band")
        headings = ("Zone", "Name", "RX (MHz)", "TX (MHz)", "TX tone", "Power", "Bandwidth")
        widths = (140, 130, 110, 110, 80, 80, 120)
        self.tree = ttk.Treeview(wrapper, columns=columns, show="headings", selectmode="extended")
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self.edit_channel())

    def _build_status(self) -> None:
        self.status = ttk.Label(self, text="", relief="sunken", anchor="w", padding=(8, 3))
        self.status.pack(side="bottom", fill="x")

    # -- helpers -----------------------------------------------------------
    def _refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        if self.codeplug is None:
            self.status.config(text="No file open. Use File > Open .xlc")
            self.title(APP_TITLE)
            return

        ordered = sorted(enumerate(self.codeplug.channels), key=lambda t: (t[1].zone, t[0]))
        for index, channel in ordered:
            zone_label = f"{channel.zone} {self.codeplug.zone_names.get(channel.zone, '')}".strip()
            self.tree.insert("", "end", iid=str(index), values=(
                zone_label,
                channel.name,
                format_frequency(channel.rx),
                format_frequency(channel.tx),
                format_tone(channel.tone),
                "High" if channel.high_power else "Low",
                "Wide" if channel.wide else "Narrow",
            ))

        counts: Dict[int, int] = {}
        for channel in self.codeplug.channels:
            counts[channel.zone] = counts.get(channel.zone, 0) + 1
        self.airband_var.set(self.codeplug.bandlimit_is_airband())
        summary = "   ".join(
            [f"{len(self.codeplug.channels)} channels"]
            + [f"Z{z}={counts[z]}" for z in sorted(counts)]
        )
        self.status.config(text=summary)
        name = os.path.basename(self.codeplug.path) if self.codeplug.path else "(new)"
        self.title(f"{APP_TITLE}  -  {name}")

    def _selected_indices(self) -> List[int]:
        return [int(i) for i in self.tree.selection()]

    def _require_open(self) -> bool:
        if self.codeplug is None:
            messagebox.showinfo(APP_TITLE, "Open a .xlc file first (File > Open).")
            return False
        return True

    # -- file actions ------------------------------------------------------
    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open .xlc file",
            filetypes=[("Codeplug files", "*.xlc"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.codeplug = Codeplug.load(path)
        except (OSError, CodeplugError) as error:
            messagebox.showerror(APP_TITLE, f"Cannot open file:\n{error}")
            return
        self._refresh()

    def _write(self, path: str) -> None:
        try:
            self.codeplug.save(path, allow_airband=self.airband_var.get(), make_backup=True)
        except (OSError, CodeplugError) as error:
            messagebox.showerror(APP_TITLE, f"Save failed:\n{error}")
            return
        messagebox.showinfo(
            APP_TITLE,
            f"Saved:\n{path}\n\nA timestamped backup of the previous file was "
            "created automatically (if it existed).",
        )
        self._refresh()

    def save_file(self) -> None:
        if not self._require_open():
            return
        if not self.codeplug.path:
            self.save_file_as()
            return
        self._write(self.codeplug.path)

    def save_file_as(self) -> None:
        if not self._require_open():
            return
        path = filedialog.asksaveasfilename(
            title="Save as", defaultextension=".xlc",
            filetypes=[("Codeplug files", "*.xlc")],
        )
        if path:
            self._write(path)

    # -- channel actions ---------------------------------------------------
    def add_channel(self) -> None:
        if not self._require_open():
            return
        dialog = ChannelDialog(self, self.codeplug.zone_names)
        self.wait_window(dialog)
        if dialog.result:
            r = dialog.result
            self.codeplug.channels.append(
                Channel(r["zone"], r["name"], r["rx"], r["tx"], r["tone"],
                        r["high_power"], r["wide"], raw=None, dirty=True)
            )
            self._refresh()

    def edit_channel(self) -> None:
        if not self._require_open():
            return
        selection = self._selected_indices()
        if len(selection) != 1:
            messagebox.showinfo(APP_TITLE, "Select exactly one channel to edit.")
            return
        channel = self.codeplug.channels[selection[0]]
        dialog = ChannelDialog(self, self.codeplug.zone_names, channel=channel)
        self.wait_window(dialog)
        if dialog.result:
            r = dialog.result
            channel.zone, channel.name = r["zone"], r["name"]
            channel.rx, channel.tx, channel.tone = r["rx"], r["tx"], r["tone"]
            channel.high_power, channel.wide = r["high_power"], r["wide"]
            channel.dirty = True
            self._refresh()

    def duplicate_channel(self) -> None:
        if not self._require_open():
            return
        selection = self._selected_indices()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Select one or more channels to duplicate.")
            return
        for index in selection:
            source = self.codeplug.channels[index]
            self.codeplug.channels.append(
                Channel(source.zone, (source.name + "_c")[:NAME_MAXLEN],
                        source.rx, source.tx, source.tone,
                        source.high_power, source.wide, raw=None, dirty=True)
            )
        self._refresh()

    def delete_channel(self) -> None:
        if not self._require_open():
            return
        selection = self._selected_indices()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Select the channels to delete.")
            return
        if not messagebox.askyesno(APP_TITLE, f"Delete {len(selection)} channel(s)?"):
            return
        for index in sorted(selection, reverse=True):
            del self.codeplug.channels[index]
        self._refresh()

    def generate_block(self) -> None:
        if not self._require_open():
            return
        dialog = BlockDialog(self, self.codeplug.zone_names)
        self.wait_window(dialog)
        if dialog.result:
            self.codeplug.channels.extend(dialog.result)
            if self.codeplug.zone_overflow():
                messagebox.showwarning(
                    APP_TITLE,
                    "One or more zones now exceed 64 channels.\n"
                    "Move some channels before saving, or the save will be blocked.",
                )
            self._refresh()

    def rename_zones(self) -> None:
        if not self._require_open():
            return
        dialog = ZoneNamesDialog(self, self.codeplug.zone_names)
        self.wait_window(dialog)
        if dialog.result:
            self.codeplug.zone_names.update(dialog.result)
            self._refresh()

    def show_about(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            APP_TITLE + "\n\n"
            "A community codeplug editor for the Baofeng UV-5RM Plus (GPS) .xlc "
            "files. Developed and tested on that specific radio via a serial "
            "cable, to speed up the clumsy zone/channel entry of the stock CPS.\n\n"
            "RECOMMENDED WORKFLOW\n"
            "1. In the CPS, read the current config FROM the radio and save it as "
            ".xlc (this is your base + first backup).\n"
            "2. Open it here; add/edit channels, generate blocks, rename zones; "
            "Save. Only channels and zones are rewritten - all other radio "
            "settings are preserved.\n"
            "3. In the CPS, open the new file and write it to the radio.\n"
            "4. Keep a backup BEFORE and AFTER writing.\n\n"
            "NOTES\n"
            "- Reverse-engineered for personal use; tested on a single radio by "
            "one person - unintended bugs are possible. Double-check a few "
            "channels in the CPS before writing.\n"
            "- Frequency, TX tone, power, bandwidth, name and zones are handled.\n"
            "- Separate RX tone and explicit AM mode are not modified.\n"
            "- Max 64 channels per zone, max 10 zones.",
        )


def main() -> None:
    EditorApp().mainloop()


if __name__ == "__main__":
    main()

"""Legacy system telemetry widget (kept minimal without unused external process hooks)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...services.system_service import SystemStatsSnapshot


class StatsTab(QWidget):
    """Real-time PC telemetry + Top processes widget."""

    def __init__(self, agents_service: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.agents_service = agents_service

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        host = QWidget()
        scroll.setWidget(host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # --- Hardware Card ---
        hw_card = QFrame()
        hw_card.setObjectName("Card")
        hw_lay = QVBoxLayout(hw_card)
        hw_lay.setContentsMargins(10, 8, 10, 10)
        hw_lay.setSpacing(6)

        grid = QGridLayout()
        grid.setSpacing(6)
        self.cpu_bar, self.cpu_val = self._metric_row(grid, 0, tr("cpu"))
        self.ram_bar, self.ram_val = self._metric_row(grid, 1, tr("memory"))
        self.gpu_bar, self.gpu_val = self._metric_row(grid, 2, tr("gpu"))
        hw_lay.addLayout(grid)

        self.io_lbl = QLabel("Disk R/W: 0 / 0 MB/s  •  Net: ↓0 / ↑0 KB/s")
        self.io_lbl.setObjectName("Muted")
        hw_lay.addWidget(self.io_lbl)

        self.cores_lbl = QLabel("")
        self.cores_lbl.setObjectName("Muted")
        self.cores_lbl.setWordWrap(True)
        hw_lay.addWidget(self.cores_lbl)

        self.battery_lbl = QLabel("")
        self.battery_lbl.setObjectName("Muted")
        hw_lay.addWidget(self.battery_lbl)
        layout.addWidget(hw_card)

        # --- Top Processes Card ---
        proc_card = QFrame()
        proc_card.setObjectName("Card")
        pv = QVBoxLayout(proc_card)
        pv.setContentsMargins(10, 8, 10, 10)
        pv.setSpacing(4)
        ph = QLabel("Top Processes")
        ph.setObjectName("Heading")
        pv.addWidget(ph)
        self.proc_list = QListWidget()
        self.proc_list.setMaximumHeight(115)
        pv.addWidget(self.proc_list)
        layout.addWidget(proc_card)

        layout.addStretch(1)

    @staticmethod
    def _metric_row(grid: QGridLayout, row: int, label: str) -> tuple[QProgressBar, QLabel]:
        name_l = QLabel(label)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        val_l = QLabel("0%")
        val_l.setMinimumWidth(115)
        grid.addWidget(name_l, row, 0)
        grid.addWidget(bar, row, 1)
        grid.addWidget(val_l, row, 2)
        return bar, val_l

    def update_stats(self, snap: SystemStatsSnapshot) -> None:
        self.cpu_bar.setValue(int(round(snap.cpu_percent)))
        freq_s = f" @ {snap.cpu_freq_mhz / 1000:.2f}GHz" if snap.cpu_freq_mhz > 0 else ""
        c_temp = f" • {snap.cpu_temp:.0f}°C" if getattr(snap, "cpu_temp", -1.0) > 0 else ""
        self.cpu_val.setText(f"{snap.cpu_percent:.0f}%{freq_s}{c_temp}")

        self.ram_bar.setValue(int(round(snap.ram_percent)))
        r_temp = f" • {snap.ram_temp:.0f}°C" if getattr(snap, "ram_temp", -1.0) > 0 else ""
        self.ram_val.setText(f"{snap.ram_used_gb:.1f}/{snap.ram_total_gb:.0f} GB ({snap.ram_percent:.0f}%){r_temp}")

        if snap.gpu_load >= 0:
            self.gpu_bar.setValue(int(round(snap.gpu_load)))
            vram = f" • {snap.gpu_vram_used_mb / 1024:.1f}/{snap.gpu_vram_total_mb / 1024:.0f}G" if snap.gpu_vram_total_mb else ""
            temp = f" • {snap.gpu_temp:.0f}°C" if snap.gpu_temp > 0 else ""
            self.gpu_val.setText(f"{snap.gpu_load:.0f}%{temp}{vram}")
        else:
            self.gpu_bar.setValue(0)
            self.gpu_val.setText("N/A")

        self.io_lbl.setText(
            f"Disk R/W: {snap.disk_read_mbs:.1f} / {snap.disk_write_mbs:.1f} MB/s  •  "
            f"Net: ↓{snap.net_rx_kbs:.0f} / ↑{snap.net_tx_kbs:.0f} KB/s"
        )
        if snap.cpu_per_core:
            cores_s = "  ".join(f"C{i}:{c:.0f}%" for i, c in enumerate(snap.cpu_per_core[:12]))
            self.cores_lbl.setText(cores_s)
        if snap.battery_percent >= 0:
            plug = "AC" if snap.battery_plugged else "Battery"
            self.battery_lbl.setText(f"{plug}: {snap.battery_percent}%")
            self.battery_lbl.show()
        else:
            self.battery_lbl.hide()

        self.proc_list.clear()
        for p in snap.top_processes:
            self.proc_list.addItem(f"{p.name} ({p.pid}) — CPU {p.cpu_pct:.1f}% • {p.mem_mb:.0f} MB")

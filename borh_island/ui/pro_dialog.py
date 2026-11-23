"""Blip PRO purchase (direct website / in-app checkout redirect), license file import, and code activation."""

from __future__ import annotations

import webbrowser
from typing import Callable

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from borh_island.core.licensing import LicenseManager, format_trial_remaining, get_license_manager


class _OrderCreateWorker(QThread):
    finished_order = pyqtSignal(bool, dict, QImage)

    def __init__(self, mgr: LicenseManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mgr = mgr

    @staticmethod
    def _resolve_web_pay_url(data: dict) -> str:
        """Resolve direct checkout URL to open in website browser / payment app."""
        pay_url = str(data.get("pay_url") or "").strip()
        if pay_url:
            return pay_url
        explicit = str(data.get("sbp_url") or data.get("nspk_url") or data.get("qr_url") or "").strip()
        return explicit

    def run(self) -> None:
        ok, data = self._mgr.create_payment_order()
        if ok:
            data["_resolved_pay_url"] = self._resolve_web_pay_url(data)
        self.finished_order.emit(ok, data, QImage())


class _OrderPollWorker(QThread):
    finished_poll = pyqtSignal(str, str)

    def __init__(
        self,
        mgr: LicenseManager,
        order_id: str,
        order_token: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mgr = mgr
        self._order_id = order_id
        self._order_token = order_token

    def run(self) -> None:
        st, msg = self._mgr.poll_payment_order(self._order_id, self._order_token)
        self.finished_poll.emit(st, msg)


class BlipProWidget(QWidget):
    """Inline Blip PRO purchase & activation view styled to match the active Island theme."""

    license_updated = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, reason: str = "", *, inline: bool = True) -> None:
        super().__init__(parent)
        self.mgr: LicenseManager = get_license_manager()
        self._inline = inline
        self._order_id = ""
        self._order_token = ""
        self._pay_url = ""
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(3000)
        self._poll_timer.timeout.connect(self._poll_order)

        if inline:
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; background: transparent;")
        hdr.addWidget(self.title_lbl, 1)

        self.close_btn = QPushButton("Назад" if inline else "Закрыть")
        self.close_btn.setObjectName("Secondary")
        self.close_btn.clicked.connect(self.close_requested.emit)
        hdr.addWidget(self.close_btn)
        root.addLayout(hdr)

        self.reason_lbl = QLabel("")
        self.reason_lbl.setWordWrap(True)
        self.reason_lbl.setVisible(False)
        root.addWidget(self.reason_lbl)

        self.perks_lbl = QLabel(
            "Стоимость Blip PRO: 300 ₽ за 3 месяца.\n"
            "Подписка PRO открывает:\n"
            "• Стекло Liquid Glass и Frost Glass\n"
            "• Погоду фона в реальном времени\n"
            "• Статы в играх, Таймер и щит курсора\n"
            "• Всех персонажей (кроме 4 эксклюзивов разработчиков)"
        )
        self.perks_lbl.setWordWrap(True)
        self.perks_lbl.setStyleSheet("font-size: 12px; background: transparent;")
        root.addWidget(self.perks_lbl)

        pay_row = QHBoxLayout()
        self.qr_btn = QPushButton("Перейти к оплате — 300 ₽ / 3 мес")
        self.qr_btn.clicked.connect(self._start_web_payment)
        pay_row.addWidget(self.qr_btn)

        self.browser_btn = QPushButton("Открыть сайт снова")
        self.browser_btn.setObjectName("Secondary")
        self.browser_btn.setVisible(False)
        self.browser_btn.clicked.connect(self._open_checkout_in_browser)
        pay_row.addWidget(self.browser_btn)

        self.embedded_pay_btn = QPushButton("Оплатить в приложении")
        self.embedded_pay_btn.setObjectName("Secondary")
        self.embedded_pay_btn.setVisible(False)
        self.embedded_pay_btn.clicked.connect(self._open_embedded_checkout)
        pay_row.addWidget(self.embedded_pay_btn)
        root.addLayout(pay_row)

        self.qr_lbl = QLabel()
        self.qr_lbl.setVisible(False)

        code_row = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("Код активации (BLIP-... или код разработчика)")
        code_row.addWidget(self.code_edit, 1)

        self.activate_btn = QPushButton("Активировать")
        self.activate_btn.clicked.connect(self._activate_code)
        code_row.addWidget(self.activate_btn)
        root.addLayout(code_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 12px; background: transparent;")
        root.addWidget(self.status_lbl)

        self.master_btn = QPushButton("🛡️ Управление кодами (Master BORh)")
        self.master_btn.setObjectName("Secondary")
        self.master_btn.setVisible(False)
        self.master_btn.clicked.connect(self._open_master_control)
        root.addWidget(self.master_btn)
        root.addStretch(1)

        self.refresh_status(reason)

    def refresh_status(self, reason: str = "") -> None:
        st = self.mgr.get_status(force_refresh=True)
        if st.is_permanent_pro:
            tier_txt = "Blip PRO (Активна • 3 месяца)" if getattr(st, "source", "") != "dev_code" else "Blip PRO (Developer)"
        elif st.tier == "TRIAL":
            tier_txt = f"Blip PRO (Триал 3 часа — осталось {format_trial_remaining(st.trial_days_left)})"
        else:
            tier_txt = "Blip FREE"
        self.title_lbl.setText(tier_txt)

        is_master = bool(getattr(st, "is_master", False))
        price_rub = 10 if is_master else 300
        self.perks_lbl.setText(
            f"Стоимость Blip PRO: {price_rub} ₽ за 3 месяца"
            f"{' (тариф Master BORh)' if is_master else ''}.\n"
            "Подписка PRO открывает:\n"
            "• Стекло Liquid Glass и Frost Glass\n"
            "• Погоду фона в реальном времени\n"
            "• Статы в играх, Таймер и щит курсора\n"
            "• Всех персонажей (кроме 4 эксклюзивов разработчиков)"
        )
        is_paid_pro = st.is_permanent_pro and getattr(st, "source", "") != "dev_code"
        if getattr(st, "payment_blocked", False):
            self.qr_btn.setEnabled(False)
            self.qr_btn.setText("Оплата отключена для вашего кода")
            self.browser_btn.setVisible(False)
            self.embedded_pay_btn.setVisible(False)
            self.qr_lbl.setVisible(False)
        elif is_paid_pro:
            self.qr_btn.setEnabled(False)
            self.qr_btn.setText("У вас уже есть Blip PRO")
            self.browser_btn.setVisible(False)
            self.embedded_pay_btn.setVisible(False)
            self.qr_lbl.setVisible(False)
        else:
            self.qr_btn.setEnabled(True)
            self.qr_btn.setText(f"Перейти к оплате — {price_rub} ₽ / 3 мес")

        self.master_btn.setVisible(is_master)

        if getattr(st, "revoked_reason", "") and not reason:
            reason = st.revoked_reason
        elif st.tier == "TRIAL" and not reason:
            reason = (
                f"У вас Trial-версия на 3 часа (осталось {format_trial_remaining(st.trial_days_left)}). "
                f"Оплатите {price_rub} ₽ за 3 месяца, если хотите активировать Blip PRO сразу."
            )
        if reason:
            self.reason_lbl.setText(reason)
            self.reason_lbl.setVisible(True)
        else:
            self.reason_lbl.setVisible(False)

    def apply_palette(self, pal) -> None:
        text_p = getattr(pal, "text_primary", "#eef2ff")
        text_m = getattr(pal, "text_muted", "#94a3b8")
        accent = getattr(pal, "accent", "#3b82f6")
        border = getattr(pal, "border", "rgba(255,255,255,48)")
        card_bg = getattr(pal, "card_bg", "rgba(255,255,255,18)")
        self.title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {text_p}; background: transparent;")
        self.reason_lbl.setStyleSheet(f"color: #fbbf24; font-size: 12px; background: transparent;")
        self.perks_lbl.setStyleSheet(f"color: {text_m}; font-size: 12px; background: transparent;")
        self.status_lbl.setStyleSheet(f"color: {text_p}; font-size: 12px; background: transparent;")
        self.setStyleSheet(
            f"QLineEdit {{ background: {card_bg}; color: {text_p}; border: 1px solid {border};"
            " border-radius: 10px; padding: 7px 11px; font-size: 13px; }"
            f"QPushButton {{ background: {accent}; color: #ffffff; border: 1px solid {border};"
            " border-radius: 10px; padding: 7px 13px; font-weight: 600; }"
            f"QPushButton#Secondary {{ background: {card_bg}; color: {text_p}; border: 1px solid {border}; }}"
        )

    def _start_web_payment(self) -> None:
        st = self.mgr.get_status()
        if st.is_permanent_pro and getattr(st, "source", "") != "dev_code":
            self.refresh_status()
            return
        prev = getattr(self, "_create_worker", None)
        if prev is not None and prev.isRunning():
            return
        self.qr_btn.setEnabled(False)
        self.status_lbl.setText("Открываем оплату на сайте...")
        worker = _OrderCreateWorker(self.mgr, self)
        worker.finished_order.connect(self._on_order_created)
        worker.finished.connect(worker.deleteLater)
        self._create_worker = worker
        worker.start()

    def _open_checkout_in_browser(self) -> None:
        if not self._pay_url:
            return
        if not QDesktopServices.openUrl(QUrl(self._pay_url)):
            webbrowser.open(self._pay_url)

    def _on_order_created(self, ok: bool, data: dict, img: QImage) -> None:
        del img
        self.qr_btn.setEnabled(True)
        if not ok:
            self.status_lbl.setText(f"Ошибка создания заказа: {data.get('error', 'нет связи')}")
            return
        self._order_id = str(data.get("order_id") or "")
        self._order_token = str(data.get("order_token") or "")
        self._pay_url = str(data.get("_resolved_pay_url") or data.get("pay_url") or "")
        amt = data.get("amount_rub", 300)
        self.qr_lbl.setVisible(False)
        self.browser_btn.setVisible(bool(self._pay_url))
        self.embedded_pay_btn.setVisible(bool(self._pay_url))
        if self._pay_url:
            self._open_checkout_in_browser()
            hint = "Страница оплаты открыта в браузере (или нажмите «Оплатить в приложении»)."
        else:
            hint = "Не удалось получить ссылку на оплату."
        self.status_lbl.setText(f"Заказ {self._order_id} на {amt} ₽. {hint} Ждём оплату...")
        self._poll_timer.start()

    def _open_embedded_checkout(self) -> None:
        if not self._pay_url:
            return
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtWebEngineCore import QWebEngineProfile
            from PyQt6.QtWebEngineWidgets import QWebEngineView

            dlg = QDialog(self)
            dlg.setWindowTitle("Оплата Blip PRO — ЮMoney / СБП")
            dlg.resize(540, 700)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(0, 0, 0, 0)
            view = QWebEngineView(dlg)
            prof = QWebEngineProfile("BorhWebAI", dlg)
            from PyQt6.QtWebEngineCore import QWebEnginePage

            view.setPage(QWebEnginePage(prof, view))
            view.setUrl(QUrl(self._pay_url))
            lay.addWidget(view)
            self._pay_dlg = dlg
            dlg.show()
        except Exception:
            webbrowser.open(self._pay_url)

    def _poll_order(self) -> None:
        if not self._order_id:
            return
        prev = getattr(self, "_poll_worker", None)
        if prev is not None and prev.isRunning():
            return
        worker = _OrderPollWorker(self.mgr, self._order_id, self._order_token, self)
        worker.finished_poll.connect(self._on_poll_finished)
        worker.finished.connect(worker.deleteLater)
        self._poll_worker = worker
        worker.start()

    def _on_poll_finished(self, st: str, msg: str) -> None:
        if st == "paid":
            self._poll_timer.stop()
            self.status_lbl.setText(msg or "Оплата получена! Blip PRO активирован.")
            self.refresh_status()
            self.license_updated.emit()
        elif st == "error" and msg:
            self.status_lbl.setText(f"Ожидаем оплату... ({msg})")

    def _activate_code(self) -> None:
        ok, msg = self.mgr.activate_code(self.code_edit.text())
        self.status_lbl.setText(msg)
        if ok:
            self._poll_timer.stop()
            self.refresh_status()
            self.license_updated.emit()

    def _open_master_control(self) -> None:
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Управление кодами (Master BORh)")
        dlg.setMinimumWidth(440)
        dlg.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        form = QFormLayout(dlg)
        code_box = QComboBox()
        code_box.setEditable(True)
        policies = self.mgr.load_code_policies()
        # Only salted SHA-256 hashes & safe labels are stored in public source (never plaintext keys!)
        known_entries = (
            ("Full Dev #1 (BLIP-FULL-C23E...)", "ff0f3d372da7c50d331548f1ce05c74f743a0e780e4bd0f0a0e2b6efe75e0bab"),
            ("Full Dev #2 (BLIP-FULL-F073...)", "148f0bed4a8bfd28eb96f2b2162a3356fa938cc2e664a7d4e5dc116280c29f20"),
            ("Full Dev #3 (BLIP-FULL-777A...)", "7c7f15fbd83b699d3e936fecd53d814f45878650fdff5ce1dd457d4470dcf3d5"),
            ("Full Dev #4 (BLIP-FULL-C5DB...)", "ccebcd27c45983769cc7fdf3502128e8373a014fb8622feda9f97f8291617a14"),
            ("Purple Star + PRO", "ce872be183e8674204aad6395a4e4f60845a6510081a973214f2022889ab2595"),
            ("KAMAZ + PRO", "9177a58a01c0c65dc1bb98c10c418ce83ec30d69d3882e5ce5f9a8232f0518c2"),
            ("Mei Mei + PRO", "50243267e7da36155d2e8dec6565132c4eab2c6bfbe37ef55b52d32dc92b32b3"),
        )
        for lbl, digest in known_entries:
            pol = policies.get(digest, {})
            tag = pol.get("mode", "active")
            code_box.addItem(f"{lbl} [{tag}]", digest)
        for digest, pol in policies.items():
            if not any(d == digest for _, d in known_entries):
                code_box.addItem(f"{pol.get('label', digest[:10])} [{pol.get('mode', 'active')}]", digest)
        mode_box = QComboBox()
        mode_box.addItem("Активен (полный доступ)", "active")
        mode_box.addItem("Бесплатный режим (FREE)", "forced_free")
        mode_box.addItem("Полная блокировка кода", "revoked")
        chk_block_pay = QCheckBox("Запретить оплату подписки этому коду")
        chk_block_pay.setChecked(True)
        feat_edit = QLineEdit()
        feat_edit.setPlaceholderText("Необязательно: glass, ai, realtime_weather, game_stats")

        status_lbl = QLabel("")
        status_lbl.setWordWrap(True)

        def _apply() -> None:
            feats = [x.strip() for x in feat_edit.text().split(",") if x.strip()]
            idx = code_box.currentIndex()
            target_val = code_box.itemData(idx) if (idx >= 0 and code_box.currentText() == code_box.itemText(idx)) else code_box.currentText().strip()
            ok, msg = self.mgr.set_code_policy(
                str(target_val or ""),
                mode=str(mode_box.currentData() or "active"),
                block_payment=bool(chk_block_pay.isChecked()),
                blocked_features=feats,
                push_github=True,
            )
            status_lbl.setText(msg)
            if ok:
                self.refresh_status()
                self.license_updated.emit()

        apply_btn = QPushButton("Применить и обновить на GitHub")
        apply_btn.clicked.connect(_apply)
        form.addRow("Код пользователя:", code_box)
        form.addRow("Режим доступа:", mode_box)
        form.addRow("", chk_block_pay)
        form.addRow("Блок функций:", feat_edit)
        form.addRow(apply_btn)
        form.addRow(status_lbl)
        dlg.exec()

    def _import_lic(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл лицензии", "", "Blip License (*.lic *.json)")
        if not path:
            return
        ok, msg = self.mgr.import_license_file(path)
        self.status_lbl.setText(msg)
        if ok:
            self._poll_timer.stop()
            self.refresh_status()
            self.license_updated.emit()

    def stop_polling(self) -> None:
        self._poll_timer.stop()


class BlipProDialog(QDialog):
    """Fallback modal dialog wrapper for purchasing Blip PRO outside the Island panel."""

    license_updated = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, reason: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Blip PRO")
        self.setMinimumWidth(420)
        self.setStyleSheet("QDialog { background: #141720; color: #eef2ff; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.widget = BlipProWidget(self, reason=reason, inline=False)
        self.widget.license_updated.connect(self.license_updated.emit)
        self.widget.close_requested.connect(self.accept)
        lay.addWidget(self.widget)
        self.mgr = self.widget.mgr
        self.qr_btn = self.widget.qr_btn
        self.code_edit = self.widget.code_edit
        self.activate_btn = self.widget.activate_btn
        self.status_lbl = self.widget.status_lbl

    def closeEvent(self, event) -> None:  # noqa: N802
        self.widget.stop_polling()
        super().closeEvent(event)


def show_pro_upgrade(
    widget: QWidget | None,
    reason: str = "",
    on_unlocked: Callable[[], None] | None = None,
) -> None:
    """Open Blip PRO payment/activation inside ExpandedPanel if available, else fallback dialog."""
    cur = widget
    panel = None
    while cur is not None:
        if hasattr(cur, "show_pro_page"):
            panel = cur
            break
        if hasattr(cur, "panel") and hasattr(cur.panel, "show_pro_page"):
            panel = cur.panel
            break
        cur = cur.parentWidget() if hasattr(cur, "parentWidget") else None

    if panel is not None:
        win = panel.window()
        if hasattr(win, "set_expanded"):
            win.set_expanded(True)
        panel.show_pro_page(reason=reason, on_unlocked=on_unlocked)
        return

    dlg = BlipProDialog(widget, reason=reason)
    if on_unlocked is not None:
        dlg.license_updated.connect(on_unlocked)
    dlg.exec()

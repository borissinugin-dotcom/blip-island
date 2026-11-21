"""39 interactive animated procedural QPainter characters with cursor, audio, busy, and rain physics."""

from __future__ import annotations

import math
import time

TARGET_FPS = 120
_ACTIVE_FRAME_MS = 8

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

# (id, Russian Name, English Name) — 15 upgraded classics + 24 new tactile 3D characters = 39 total
CHARACTER_DEFS: list[tuple[str, str, str]] = [
    ("liquid", "Жидкий ферромагнит", "Ferrofluid Blob"),
    ("orb", "Плазменная сфера", "Plasma Core"),
    ("cat", "Кибер-кот", "Cyber Kitty"),
    ("fox", "Лисичка", "Flame Fox"),
    ("shiba", "Сиба-ину", "Shiba Inu"),
    ("bot", "Астро-бот", "Astro Bot"),
    ("owl", "Лунная сова", "Night Owl"),
    ("ghost", "Неон-призрак", "Wisp Phantom"),
    ("dragon", "Дракончик", "Ember Dragon"),
    ("octopus", "Осьминожек", "Tide Octo"),
    ("alien", "Космо-пришелец", "Cosmo Alien"),
    ("panda", "Панда-моти", "Mochi Panda"),
    ("axolotl", "Аксолотль", "Sakura Axolotl"),
    ("crystal", "Кристалл-дух", "Prism Spirit"),
    ("slime", "Капля-слайм", "Dew Slime"),
    ("frog", "Квак-моти", "Mochi Frog"),
    ("penguin", "Пингви", "Chill Penguin"),
    ("bee", "Шмель-би", "Honey Bee"),
    ("bunny", "Луна-кролик", "Star Bunny"),
    ("capybara", "Капибара", "Zen Capybara"),
    ("duck", "Утёнок", "Rubber Duck"),
    ("koala", "Коала", "Eucalyptus Koala"),
    ("raccoon", "Енот-бандит", "Bandit Raccoon"),
    ("unicorn", "Единорог", "Nebula Unicorn"),
    ("red_panda", "Красная панда", "Red Panda"),
    ("whale", "Китёнок", "Cloud Whale"),
    ("shark", "Акулёнок", "Plush Shark"),
    ("mushroom", "Мухоморчик", "Forest Shroom"),
    ("star", "Звёздочка", "Astral Star"),
    ("moon", "Полумесяц", "Dreamy Moon"),
    ("cloud", "Тучка", "Nimbus Cloud"),
    ("flame", "Огонёк", "Ember Wisp"),
    ("boba", "Боба-чай", "Boba Cup"),
    ("dino", "Дино-пиксель", "Mint Dino"),
    ("phoenix", "Феникс", "Solar Phoenix"),
    ("borscht", "Борщ", "Borscht"),
    ("purple_star", "Фиолетовая звезда", "Purple Star"),
    ("kamaz", "КАМАЗ / БелАЗ", "KAMAZ / BelAZ"),
    ("meimei", "Барс Мэй Мэй", "Mei Mei Snow Leopard"),
    ("claude", "Клод", "Claude"),
    ("gemini", "Джемини", "Gemini"),
    ("chatgpt", "ChatGPT", "ChatGPT"),
    ("deepseek", "ДипСик", "DeepSeek"),
    ("grok", "Грок", "Grok"),
    ("perplexity", "Перплексити", "Perplexity"),
    ("qwen", "Квен", "Qwen"),
    ("llama", "Ллама", "Llama"),
    ("mistral", "Мистраль", "Mistral"),
    ("copilot", "Копайлот", "Copilot"),
    ("kuro_neko", "Куро-нэко", "Kuro Neko"),
    ("sakura_kitsune", "Сакура-кицунэ", "Sakura Kitsune"),
    ("cyber_skull", "Кибер-череп", "Cyber Skull"),
    ("void_eye", "Око Бездны", "Void Eye"),
    ("mech_unit", "Меха-юнит", "Mech Unit"),
    ("ninja", "Тень-ниндзя", "Shadow Ninja"),
    ("samurai", "Кибер-самурай", "Cyber Samurai"),
    ("coffee_cup", "Эспрессо", "Coffee Buddy"),
    ("sushi_roll", "Суши-ролл", "Sushi Roll"),
    ("cassette", "Ретро-кассета", "Retro Tape"),
    ("vinyl_disc", "Винил-DJ", "Vinyl Disc"),
    ("pixel_heart", "Пиксель-сердце", "8-Bit Heart"),
    ("planet_saturn", "Сатурн-рин", "Ringed Saturn"),
    ("lightning_spark", "Вольт-искра", "Volt Spark"),
    ("pumpkin_jack", "Тыковка Джек", "Jack Lantern"),
    ("tamagotchi", "Тамагочи", "Pocket Pet"),
    ("astro_helmet", "Космонавт", "Astronaut"),
    ("crystal_fox", "Ледяной лис", "Frost Fox"),
]


def _c(hex_or_color: str | QColor, alpha: int = 255) -> QColor:
    c = QColor(hex_or_color)
    if alpha < 255:
        c.setAlpha(max(0, min(255, alpha)))
    return c


class CharacterWidget(QWidget):
    """Animated 3D-styled mascot driven by cursor gaze, audio energy, activity state, and weather."""

    poked = pyqtSignal()

    def __init__(self, character_id: str = "liquid", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.character_id = character_id
        self._cursor_dx = 0.0
        self._cursor_dy = 0.0
        self._target_cursor_dx = 0.0
        self._target_cursor_dy = 0.0
        self._audio_level = 0.0
        self._target_audio_level = 0.0
        self._thinking = False
        self._alarm = False
        self._precip = "none"
        self._activity_state = "idle"  # idle | sleeping | music | working | curious | game
        self._accent = QColor("#38BDF8")
        self._phase = 0.0
        self._bounce_phase = 0.0
        self._smooth_bounce_amp = 0.85
        self._blink_t = 0.0
        self._poke_energy = 0.0
        self._sleep_phase = 0.0

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._animated_enabled = True
        self._anim = QTimer(self)
        self._anim.setTimerType(Qt.TimerType.CoarseTimer)
        self._anim.setInterval(48)
        self._anim.timeout.connect(self._tick)
        self._anim.start()

    def set_character(self, character_id: str) -> None:
        self.character_id = character_id
        self.update()

    def set_animated(self, enabled: bool) -> None:
        """Start or stop animation timer (used by gallery thumbnails)."""
        self._animated_enabled = bool(enabled)
        if self._animated_enabled and self.isVisible():
            if not self._anim.isActive():
                self._anim.start()
        elif self._anim.isActive():
            self._anim.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_animated_enabled", True) and not self._anim.isActive():
            self._anim.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._anim.isActive():
            self._anim.stop()
        super().hideEvent(event)

    def set_accent(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if c.isValid():
            self._accent = c

    def set_cursor_norm(self, dx: float, dy: float) -> None:
        self._target_cursor_dx = max(-1.0, min(1.0, dx))
        self._target_cursor_dy = max(-1.0, min(1.0, dy))

    def set_audio_level(self, level: float) -> None:
        self._target_audio_level = max(0.0, min(1.0, level))

    def set_thinking(self, thinking: bool) -> None:
        if self._thinking != bool(thinking):
            self._thinking = bool(thinking)
            self.update()

    def set_alarm(self, alarm: bool) -> None:
        if self._alarm != bool(alarm):
            self._alarm = bool(alarm)
            self.update()

    def set_precip(self, precip: str) -> None:
        self._precip = precip

    def set_activity_state(self, state: str) -> None:
        """Set contextual companion state: idle | sleeping | music | working | curious | game."""
        norm = (state or "idle").strip().lower()
        if norm != self._activity_state:
            self._activity_state = norm
            self.update()

    def trigger_poke(self) -> None:
        self._poke_energy = 1.0
        self.poked.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.trigger_poke()
            event.accept()
            return
        super().mousePressEvent(event)

    def step_frame(self, dt_scale: float = 1.0, *, schedule_update: bool = True) -> None:
        """Advance character phase, blink, audio/cursor damping, and secondary animations."""
        s = max(0.01, dt_scale)
        # Smooth exponential envelope on audio peaks and gaze cursor tracking
        tgt_a = getattr(self, "_target_audio_level", self._audio_level)
        k_audio = 1.0 - ((0.64 if tgt_a > self._audio_level else 0.84) ** s)
        self._audio_level += (tgt_a - self._audio_level) * k_audio
        k_cur = 1.0 - (0.76 ** s)
        self._cursor_dx += (getattr(self, "_target_cursor_dx", self._cursor_dx) - self._cursor_dx) * k_cur
        self._cursor_dy += (getattr(self, "_target_cursor_dy", self._cursor_dy) - self._cursor_dy) * k_cur

        if self._activity_state == "sleeping":
            speed = 0.045
            b_mult = 1.40
        elif self._activity_state == "music":
            speed = 0.14 + self._audio_level * 0.28
            b_mult = 2.35
        elif self._activity_state in ("working", "game"):
            speed = 0.15
            b_mult = 1.50
        else:
            speed = 0.08 + self._audio_level * 0.22
            b_mult = 1.50

        self._phase += speed * s
        # Continuous bounce phase integration prevents sudden phase jumps when changing state
        self._bounce_phase = getattr(self, "_bounce_phase", self._phase * 1.5) + speed * b_mult * s

        win = self.window()
        collapsed_island = win is not None and hasattr(win, "_watch_timer") and not getattr(win, "_expanded", False)
        if self._activity_state == "sleeping":
            tgt_amp = 0.45
        elif collapsed_island:
            tgt_amp = 0.85 + self._audio_level * 1.25
        else:
            tgt_amp = 1.1 + self._audio_level * 2.15
        self._smooth_bounce_amp = getattr(self, "_smooth_bounce_amp", tgt_amp) + (tgt_amp - getattr(self, "_smooth_bounce_amp", tgt_amp)) * (1.0 - (0.82 ** s))

        self._sleep_phase = (self._sleep_phase + 0.03 * s) % 1.0
        self._poke_energy = max(0.0, self._poke_energy - 0.07 * s)
        if self._blink_t > 0:
            self._blink_t = max(0.0, self._blink_t - 0.15 * s)
        elif self._activity_state != "sleeping" and int(self._phase * 14) % 95 == 0:
            self._blink_t = 1.0
        if schedule_update:
            self.update()

    def step_frame_synced(self, dt_scale: float = 1.0) -> None:
        """Advance character state in lockstep with parent IslandWindow repaint (avoids duplicate UpdateLayeredWindow)."""
        self._last_synced_ts = time.monotonic()
        self.step_frame(dt_scale, schedule_update=False)

    def _tick(self) -> None:
        if not self.isVisible():
            return
        win = self.window()
        if win is not None and (not win.isVisible() or getattr(win, "_hide_state", "shown") == "hidden"):
            return
        # When hosted inside an expanded IslandWindow, _watch_timer already repaints the full layered window
        if win is not None and getattr(win, "_expanded", False):
            return
        now = time.monotonic()
        is_island = win is not None and hasattr(win, "_watch_timer")
        if is_island and (now - getattr(self, "_last_synced_ts", 0.0)) < 0.06:
            # Parent IslandWindow._update_liquid_physics is actively driving frames; avoid double-stepping
            self._last_tick_ts = now
            return
        layered = win is not None and win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        active = (
            self._audio_level > 0.08
            or self._poke_energy > 0.02
            or self._thinking
            or self._alarm
            or (is_island and getattr(win, "_music_strip_open", False))
        )
        if is_island:
            if active:
                want_ms = 22
            elif self._activity_state == "sleeping":
                want_ms = 115
            elif self._activity_state == "curious":
                want_ms = 42
            else:
                want_ms = 70
        else:
            want_ms = 24 if active else (55 if layered else 42)
        if self._anim.interval() != want_ms:
            self._anim.setInterval(want_ms)
        last = getattr(self, "_last_tick_ts", 0.0)
        dt = max(0.001, min(0.08, now - last)) if last > 0.0 else (want_ms / 1000.0)
        self._last_tick_ts = now
        scale = (dt / 0.033) * (0.50 if is_island else 1.0)
        self.step_frame(scale)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w * 0.5, h * 0.52
        b_ph = getattr(self, "_bounce_phase", self._phase * 1.5)
        b_amp = getattr(self, "_smooth_bounce_amp", 0.85)
        bounce = math.sin(b_ph) * b_amp
        win = self.window()
        collapsed_island = win is not None and hasattr(win, "_watch_timer") and not getattr(win, "_expanded", False)
        tilt_amp = (1.5 + 2.4 * self._audio_level) if self._activity_state == "music" else (0.55 if self._activity_state != "sleeping" else 0.35)
        if collapsed_island:
            tilt_amp *= 0.62
        tilt_deg = math.sin(b_ph * 0.5) * tilt_amp
        squish = (
            1.0
            + self._poke_energy * 0.18 * math.sin(self._poke_energy * 14.0)
            + math.cos(b_ph) * (0.020 if self._activity_state == "music" else 0.008)
        )
        r = min(w, h) * 0.35

        p.save()
        p.translate(cx, cy + bounce)
        p.rotate(tilt_deg)
        p.scale(squish, 2.0 - squish)
        p.translate(-cx, -cy)

        self._draw_ambient_glow(p, cx, cy, r)

        cid = self.character_id
        dispatch = getattr(self, "_dispatch_cache", None)
        if dispatch is None:
            dispatch = {
                "liquid": self._draw_liquid,
                "orb": self._draw_orb,
                "cat": lambda pp, x, y, rr: self._draw_eared_mammal(
                    pp, x, y, rr, "#C084FC", "#7E22CE", "#F9A8D4", cheek="#F472B6", whiskers=True, ear_twitch=True
                ),
                "fox": lambda pp, x, y, rr: self._draw_eared_mammal(
                    pp, x, y, rr, "#FB923C", "#C2410C", "#FEF3C7", cheek="#FDBA74", muzzle_patch=True, tail_colors=("#EA580C", "#FEF3C7")
                ),
                "shiba": lambda pp, x, y, rr: self._draw_eared_mammal(
                    pp, x, y, rr, "#FCD34D", "#B45309", "#FFFBEB", cheek="#FDA4AF", brows=True, muzzle_patch=True, tail_colors=("#F59E0B", "#FFFBEB")
                ),
                "bot": self._draw_bot,
                "owl": self._draw_owl,
                "ghost": self._draw_ghost,
                "dragon": self._draw_dragon,
                "octopus": self._draw_octopus,
                "alien": self._draw_alien,
                "panda": self._draw_panda,
                "axolotl": self._draw_axolotl,
                "crystal": self._draw_crystal,
                "slime": self._draw_slime,
                "frog": self._draw_frog,
                "penguin": self._draw_penguin,
                "bee": self._draw_bee,
                "bunny": self._draw_bunny,
                "capybara": self._draw_capybara,
                "duck": self._draw_duck,
                "koala": self._draw_koala,
                "raccoon": self._draw_raccoon,
                "unicorn": self._draw_unicorn,
                "red_panda": self._draw_red_panda,
                "whale": self._draw_whale,
                "shark": self._draw_shark,
                "mushroom": self._draw_mushroom,
                "star": self._draw_star,
                "moon": self._draw_moon,
                "cloud": self._draw_cloud,
                "flame": self._draw_flame,
                "boba": self._draw_boba,
                "dino": self._draw_dino,
                "phoenix": self._draw_phoenix,
                "borscht": self._draw_borscht,
                "purple_star": self._draw_purple_star,
                "kamaz": self._draw_kamaz,
                "meimei": self._draw_meimei,
                "claude": self._draw_claude,
                "gemini": self._draw_gemini,
                "chatgpt": self._draw_chatgpt,
                "deepseek": self._draw_deepseek,
                "grok": self._draw_grok,
                "perplexity": self._draw_perplexity,
                "qwen": self._draw_qwen,
                "llama": self._draw_llama,
                "mistral": self._draw_mistral,
                "copilot": self._draw_copilot,
                "kuro_neko": self._draw_kuro_neko,
                "sakura_kitsune": self._draw_sakura_kitsune,
                "cyber_skull": self._draw_cyber_skull,
                "void_eye": self._draw_void_eye,
                "mech_unit": self._draw_mech_unit,
                "ninja": self._draw_ninja,
                "samurai": self._draw_samurai,
                "coffee_cup": self._draw_coffee_cup,
                "sushi_roll": self._draw_sushi_roll,
                "cassette": self._draw_cassette,
                "vinyl_disc": self._draw_vinyl_disc,
                "pixel_heart": self._draw_pixel_heart,
                "planet_saturn": self._draw_planet_saturn,
                "lightning_spark": self._draw_lightning_spark,
                "pumpkin_jack": self._draw_pumpkin_jack,
                "tamagotchi": self._draw_tamagotchi,
                "astro_helmet": self._draw_astro_helmet,
                "crystal_fox": self._draw_crystal_fox,
            }
            self._dispatch_cache = dispatch
        fn = dispatch.get(cid, self._draw_slime)
        if self._alarm:
            sh = math.sin(self._phase * 9.0) * r * 0.08
            fn(p, cx + sh, cy, r)
        else:
            fn(p, cx, cy, r)
        p.restore()

        if self._alarm:
            self._draw_alarm_halo(p, cx, cy, r * 1.24)
        if self._thinking:
            self._draw_thinking_ring(p, cx, cy, r * 1.26)
        if self._precip in ("rain", "snow", "storm"):
            self._draw_precip_drop(p, cx, cy, r)
        if self._activity_state == "sleeping":
            self._draw_sleep_zzz(p, cx, cy, r)
        elif self._activity_state == "music" and self._audio_level > 0.08:
            self._draw_music_note(p, cx, cy, r)
        elif self._activity_state == "idle":
            self._draw_idle_spark(p, cx, cy, r)
        p.end()

    # ------------------------------------------------------------------ Shared 3D Studio Helpers
    def _draw_ambient_glow(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        g = QRadialGradient(QPointF(cx, cy + r * 0.14), r * 1.42)
        g.setColorAt(0.0, _c(self._accent, 52))
        g.setColorAt(0.65, _c(self._accent, 16))
        g.setColorAt(1.0, _c(self._accent, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy + r * 0.10), r * 1.40, r * 1.22)

    @staticmethod
    def _sphere_brush(cx: float, cy: float, r: float, light: str | QColor, mid: str | QColor, dark: str | QColor) -> QRadialGradient:
        g = QRadialGradient(QPointF(cx - r * 0.34, cy - r * 0.38), r * 1.48)
        g.setColorAt(0.0, QColor(light))
        g.setColorAt(0.55, QColor(mid))
        g.setColorAt(1.0, QColor(dark))
        return g

    def _draw_3d_dome(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        light: str | QColor,
        mid: str | QColor,
        dark: str | QColor,
        rim_alpha: int = 120,
    ) -> None:
        p.setBrush(self._sphere_brush(cx, cy, max(rx, ry), light, mid, dark))
        p.setPen(QPen(QColor(255, 255, 255, rim_alpha), max(0.8, min(rx, ry) * 0.06)))
        p.drawEllipse(QPointF(cx, cy), rx, ry)
        # Top-left glossy capsule specular reflection
        p.save()
        p.translate(cx - rx * 0.34, cy - ry * 0.42)
        p.rotate(-24)
        spec = QLinearGradient(0, -ry * 0.16, 0, ry * 0.16)
        spec.setColorAt(0.0, QColor(255, 255, 255, 130))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(spec)
        p.drawEllipse(QPointF(0.0, 0.0), rx * 0.36, ry * 0.16)
        p.restore()

    def _draw_cheeks(self, p: QPainter, cx: float, cy: float, sep: float, r: float, color: str | QColor = "#FB7185") -> None:
        c = _c(color, 135)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(cx - sep * 1.18, cy + r * 0.16), r * 0.18, r * 0.11)
        p.drawEllipse(QPointF(cx + sep * 1.18, cy + r * 0.16), r * 0.18, r * 0.11)

    def _draw_eyes(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        sep: float,
        er: float,
        color: QColor | str = "#FFFFFF",
        pupil_hex: str = "#0F172A",
        happy: bool = False,
    ) -> None:
        look_x = self._cursor_dx * er * 0.45
        look_y = self._cursor_dy * er * 0.38
        if self._alarm:
            er = er * 1.26
            look_x += math.sin(self._phase * 14.0) * er * 0.14
        sleeping = self._activity_state == "sleeping" and not self._alarm
        eye_c = QColor(color)
        for sign in (-1.0, 1.0):
            ex = cx + sign * sep
            ey = cy
            if (sleeping or self._blink_t > 0.42 or happy) and not self._alarm:
                p.setPen(QPen(QColor(pupil_hex) if eye_c.lightness() > 165 else eye_c, max(1.6, er * 0.48), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                arc_r = QRectF(ex - er, ey - er * 0.45, er * 2, er * 1.1)
                start_a = 15 * 16 if happy else 200 * 16
                span_a = 150 * 16 if happy else 140 * 16
                p.drawArc(arc_r, start_a, span_a)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                # Outer sclera or luminous bulb
                p.setBrush(eye_c)
                p.drawEllipse(QPointF(ex, ey), er * 1.05, er * 1.12)
                if eye_c.lightness() > 130:
                    # Deep glossy iris inside white sclera
                    p.setBrush(QColor(pupil_hex))
                    p.drawEllipse(QPointF(ex + look_x * 0.72, ey + look_y * 0.72), er * 0.72, er * 0.78)
                # Primary crisp catchlight
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawEllipse(QPointF(ex - er * 0.24 + look_x * 0.3, ey - er * 0.30 + look_y * 0.3), er * 0.34, er * 0.34)
                # Secondary micro-glint
                p.setBrush(QColor(255, 255, 255, 165))
                p.drawEllipse(QPointF(ex + er * 0.26, ey + er * 0.24), er * 0.14, er * 0.14)

    def _draw_smile(self, p: QPainter, cx: float, cy: float, r: float, col: str = "#1E293B", width_scale: float = 0.22) -> None:
        p.setPen(QPen(QColor(col), max(1.2, r * 0.09), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(cx - r * width_scale, cy)
        path.quadTo(cx, cy + r * 0.16, cx + r * width_scale, cy)
        p.drawPath(path)

    # ------------------------------------------------------------------ 15 Upgraded Classic Characters
    def _draw_liquid(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        path = QPainterPath()
        pts = 28
        poly: list[QPointF] = []
        for i in range(pts):
            a = (i / pts) * math.tau
            wob = (
                math.sin(a * 3 + self._phase) * 0.08
                + math.cos(a * 2 - self._phase * 1.25) * 0.06
                + self._audio_level * 0.14 * math.sin(a * 4 + self._phase * 2)
            )
            rad = r * (0.98 + wob)
            poly.append(QPointF(cx + math.cos(a) * rad, cy + math.sin(a) * rad))
        path.moveTo((poly[0] + poly[-1]) * 0.5)
        for i in range(pts):
            nxt = poly[(i + 1) % pts]
            mid = (poly[i] + nxt) * 0.5
            path.quadTo(poly[i], mid)
        path.closeSubpath()

        g = self._sphere_brush(cx, cy, r, _c(self._accent).lighter(145), "#1E293B", "#020617")
        p.setBrush(g)
        p.setPen(QPen(_c(self._accent, 210), max(1.4, r * 0.09)))
        p.drawPath(path)
        # Orbiting iridescent highlight & micro-droplet satellite
        sh_a = self._phase * 1.1
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 115))
        p.drawEllipse(QPointF(cx - r * 0.34 + math.cos(sh_a) * r * 0.08, cy - r * 0.42 + math.sin(sh_a) * r * 0.05), r * 0.28, r * 0.12)
        sat_a = self._phase * 1.8
        p.setBrush(_c(self._accent, 210))
        p.drawEllipse(QPointF(cx + math.cos(sat_a) * r * 1.18, cy + math.sin(sat_a) * r * 0.92), r * 0.09, r * 0.09)
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.31, r * 0.19, color=self._accent.lighter(155), pupil_hex="#0F172A")

    def _draw_orb(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Rotating plasma containment rings + chasing photon dots
        for idx_r, (tilt, alpha) in enumerate(((28, 175), (-28, 125))):
            ang_deg = tilt + math.sin(self._phase * 0.9) * (18.0 if idx_r == 0 else -18.0)
            p.save()
            p.translate(cx, cy)
            p.rotate(ang_deg)
            p.setPen(QPen(_c(self._accent, alpha), max(1.2, r * 0.08)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(0.0, 0.0), r * 1.18, r * 0.42)
            orb_a = self._phase * (2.6 if idx_r == 0 else -2.2) + idx_r * 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(QPointF(math.cos(orb_a) * r * 1.18, math.sin(orb_a) * r * 0.42), r * 0.10, r * 0.10)
            p.restore()
        # Translucent energy core
        core_r = r * (0.84 + self._audio_level * 0.09)
        self._draw_3d_dome(p, cx, cy, core_r, core_r, "#FFFFFF", self._accent, _c(self._accent).darker(185), rim_alpha=185)
        self._draw_eyes(p, cx, cy, r * 0.28, r * 0.17, color="#FFFFFF", pupil_hex="#0F172A")

    def _draw_eared_mammal(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        light: str,
        dark: str,
        inner: str,
        *,
        cheek: str = "#FB7185",
        whiskers: bool = False,
        brows: bool = False,
        muzzle_patch: bool = False,
        ear_twitch: bool = False,
        tail_colors: tuple[str, str] | None = None,
    ) -> None:
        mid = _c(light).darker(118)
        if tail_colors is not None:
            t_col, t_tip = tail_colors
            wag = math.sin(self._phase * 3.1) * r * 0.18
            p.save()
            p.translate(cx + r * 0.72, cy + r * 0.45)
            p.rotate(22.0 + math.sin(self._phase * 3.1) * 18.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t_col))
            p.drawEllipse(QPointF(r * 0.18, -r * 0.22 + wag * 0.25), r * 0.24, r * 0.44)
            p.setBrush(QColor(t_tip))
            p.drawEllipse(QPointF(r * 0.20, -r * 0.48 + wag * 0.25), r * 0.16, r * 0.20)
            p.restore()

        for sign in (-1.0, 1.0):
            tw = math.sin(self._phase * 3.4 + (0.0 if sign < 0 else 1.7)) * 6.5 if ear_twitch else math.sin(self._phase * 1.8) * 2.5 * sign
            p.save()
            p.translate(cx + sign * r * 0.45, cy - r * 0.45)
            p.rotate(tw * sign)
            p.translate(-(cx + sign * r * 0.45), -(cy - r * 0.45))
            ear = QPolygonF([
                QPointF(cx + sign * r * 0.24, cy - r * 0.56),
                QPointF(cx + sign * r * 0.86, cy - r * 1.06),
                QPointF(cx + sign * r * 0.90, cy - r * 0.24),
            ])
            p.setBrush(QColor(dark))
            p.setPen(QPen(QColor(255, 255, 255, 90), max(0.8, r * 0.05)))
            p.drawPolygon(ear)
            in_ear = QPolygonF([
                QPointF(cx + sign * r * 0.38, cy - r * 0.58),
                QPointF(cx + sign * r * 0.78, cy - r * 0.92),
                QPointF(cx + sign * r * 0.78, cy - r * 0.36),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(inner))
            p.drawPolygon(in_ear)
            p.restore()

        self._draw_3d_dome(p, cx, cy + r * 0.04, r * 0.96, r * 0.86, light, mid, dark)

        if muzzle_patch:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c(inner, 235))
            p.drawEllipse(QPointF(cx, cy + r * 0.34), r * 0.68, r * 0.42)
        if brows:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(inner))
            brow_dy = abs(math.sin(self._phase * 2.6)) * r * 0.055
            for sign in (-1.0, 1.0):
                p.drawEllipse(QPointF(cx + sign * r * 0.32, cy - r * 0.30 - brow_dy), r * 0.13, r * 0.08)

        self._draw_cheeks(p, cx, cy + r * 0.06, r * 0.40, r, cheek)
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.34, r * 0.18, color="#FFFFFF", pupil_hex="#18181B")

        # Cute triangular nose + :3 mouth
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F472B6" if whiskers else "#1E293B"))
        p.drawEllipse(QPointF(cx, cy + r * 0.18), r * 0.11, r * 0.08)
        self._draw_smile(p, cx, cy + r * 0.28, r, "#1E293B", 0.22)

        if whiskers:
            w_sway = math.sin(self._phase * 2.5) * r * 0.06
            p.setPen(QPen(QColor(255, 255, 255, 165), max(1.0, r * 0.05)))
            for s in (-1.0, 1.0):
                for dy in (-0.06, 0.12):
                    p.drawLine(QPointF(cx + s * r * 0.48, cy + r * (0.14 + dy)), QPointF(cx + s * r * 0.96, cy + r * (0.06 + dy * 1.5) + w_sway * s))

    def _draw_bot(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Swaying antenna + pulsing LED beacon
        ant_dx = math.sin(self._phase * 2.1) * r * 0.10
        p.setPen(QPen(QColor("#94A3B8"), max(1.6, r * 0.10), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy - r * 0.78), QPointF(cx + ant_dx, cy - r * 1.12))
        pulse = int(155 + 100 * math.sin(self._phase * 3.4))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(self._accent, pulse))
        p.drawEllipse(QPointF(cx + ant_dx, cy - r * 1.16), r * 0.18, r * 0.18)

        # Side ear pods pulsing softly
        p.setBrush(QColor("#475569"))
        for sign in (-1.0, 1.0):
            pod_y = math.sin(self._phase * 2.8 + sign) * r * 0.04
            p.drawRoundedRect(QRectF(cx + sign * r * 0.95 - r * 0.12, cy - r * 0.26 + pod_y, r * 0.24, r * 0.56), r * 0.1, r * 0.1)

        # Metallic capsule helmet
        body = QLinearGradient(cx - r, cy - r, cx + r, cy + r)
        body.setColorAt(0.0, QColor("#E2E8F0"))
        body.setColorAt(0.5, QColor("#64748B"))
        body.setColorAt(1.0, QColor("#1E293B"))
        p.setBrush(body)
        p.setPen(QPen(QColor(255, 255, 255, 140), max(1.0, r * 0.06)))
        p.drawRoundedRect(QRectF(cx - r * 0.94, cy - r * 0.78, r * 1.88, r * 1.58), r * 0.52, r * 0.52)

        # Dark glass visor + sweeping cyan laser scan line + LED eyes
        p.setBrush(QColor("#020617"))
        p.setPen(QPen(_c(self._accent, 190), max(1.1, r * 0.06)))
        p.drawRoundedRect(QRectF(cx - r * 0.72, cy - r * 0.42, r * 1.44, r * 0.90), r * 0.34, r * 0.34)
        scan_x = cx + math.sin(self._phase * 2.3) * r * 0.46
        p.setPen(QPen(_c(self._accent, 130), max(1.2, r * 0.08), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(scan_x, cy - r * 0.30), QPointF(scan_x, cy + r * 0.36))
        self._draw_eyes(p, cx, cy + r * 0.02, r * 0.34, r * 0.18, color="#38BDF8", pupil_hex="#38BDF8")

    def _draw_owl(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        tilt = math.sin(self._phase * 1.4) * 7.5
        flap = math.sin(self._phase * 3.0) * 16.0
        p.save()
        p.translate(cx, cy)
        p.rotate(tilt)
        p.translate(-cx, -cy)
        # Flapping wing nubs
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#312E81"))
        for sign in (-1.0, 1.0):
            p.save()
            p.translate(cx + sign * r * 0.80, cy + r * 0.25)
            p.rotate(sign * flap)
            p.drawEllipse(QPointF(sign * r * 0.12, 0.0), r * 0.24, r * 0.42)
            p.restore()
        # Ear tufts
        for sign in (-1.0, 1.0):
            tuft = QPolygonF([
                QPointF(cx + sign * r * 0.30, cy - r * 0.68),
                QPointF(cx + sign * r * 0.88, cy - r * 1.06),
                QPointF(cx + sign * r * 0.78, cy - r * 0.36),
            ])
            p.drawPolygon(tuft)
        self._draw_3d_dome(p, cx, cy + r * 0.05, r * 0.94, r * 0.92, "#818CF8", "#4F46E5", "#1E1B4B")
        # Facial feather rings + cream belly oval
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E0E7FF"))
        p.drawEllipse(QPointF(cx, cy + r * 0.56), r * 0.56, r * 0.38)
        for sign in (-1.0, 1.0):
            p.setBrush(QColor("#312E81"))
            p.drawEllipse(QPointF(cx + sign * r * 0.34, cy - r * 0.08), r * 0.36, r * 0.36)
        self._draw_eyes(p, cx, cy - r * 0.08, r * 0.34, r * 0.21, color="#FEF08A", pupil_hex="#0F172A")
        p.setBrush(QColor("#F59E0B"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(cx - r * 0.15, cy + r * 0.08), QPointF(cx + r * 0.15, cy + r * 0.08), QPointF(cx, cy + r * 0.30)]))
        p.restore()

    def _draw_ghost(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        drift_x = math.sin(self._phase * 1.4) * r * 0.09
        cx += drift_x
        path = QPainterPath()
        path.moveTo(cx - r * 0.88, cy + r * 0.08)
        path.arcTo(QRectF(cx - r * 0.88, cy - r * 0.95, r * 1.76, r * 1.80), 180, -180)
        wave = math.sin(self._phase * 2.5) * r * 0.12
        path.lineTo(cx + r * 0.88, cy + r * 0.72 + wave)
        path.quadTo(cx + r * 0.55, cy + r * 0.98 - wave, cx + r * 0.28, cy + r * 0.72)
        path.quadTo(cx, cy + r * 1.00 + wave, cx - r * 0.30, cy + r * 0.72)
        path.quadTo(cx - r * 0.60, cy + r * 0.96 - wave, cx - r * 0.88, cy + r * 0.70)
        path.closeSubpath()

        g = self._sphere_brush(cx, cy - r * 0.2, r * 1.1, "#F8FAFC", "#C7D2FE", "#6366F1")
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 165), max(1.0, r * 0.06)))
        p.drawPath(path)
        # Rising ectoplasm spirit wisp
        orb_t = (self._phase * 0.5) % 1.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(224, 231, 255, int(190 * (1.0 - orb_t))))
        p.drawEllipse(QPointF(cx - r * 0.88 + math.sin(self._phase * 2) * r * 0.1, cy - r * 0.2 - orb_t * r * 0.8), r * 0.10, r * 0.10)
        self._draw_cheeks(p, cx, cy + r * 0.08, r * 0.38, r, "#A78BFA")
        self._draw_eyes(p, cx, cy - r * 0.14, r * 0.31, r * 0.19, color="#FFFFFF", pupil_hex="#1E1B4B")

    def _draw_dragon(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Flapping emerald bat wings + glowing ember sparks
        wflap = math.sin(self._phase * 3.2) * 18.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#047857"))
        for sign in (-1.0, 1.0):
            p.save()
            p.translate(cx + sign * r * 0.72, cy + r * 0.06)
            p.rotate(sign * wflap)
            wing = QPolygonF([
                QPointF(0, 0),
                QPointF(sign * r * 0.56, -r * 0.42),
                QPointF(sign * r * 0.42, r * 0.24),
            ])
            p.drawPolygon(wing)
            p.restore()
        # Curved golden horns
        p.setBrush(QColor("#FBBF24"))
        for sign in (-1.0, 1.0):
            horn = QPolygonF([
                QPointF(cx + sign * r * 0.24, cy - r * 0.62),
                QPointF(cx + sign * r * 0.72, cy - r * 1.15),
                QPointF(cx + sign * r * 0.62, cy - r * 0.54),
            ])
            p.drawPolygon(horn)
        self._draw_3d_dome(p, cx, cy + r * 0.06, r * 0.96, r * 0.84, "#34D399", "#059669", "#064E3B")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#A7F3D0"))
        p.drawEllipse(QPointF(cx, cy + r * 0.44), r * 0.64, r * 0.34)
        self._draw_eyes(p, cx, cy - r * 0.06, r * 0.33, r * 0.19, color="#FEF08A", pupil_hex="#022C22")
        # Nostrils + rising orange fire ember spark
        self._draw_cheeks(p, cx, cy + r * 0.14, r * 0.42, r, "#FB923C")
        p.setBrush(QColor("#064E3B"))
        p.drawEllipse(QPointF(cx - r * 0.15, cy + r * 0.24), r * 0.06, r * 0.05)
        p.drawEllipse(QPointF(cx + r * 0.15, cy + r * 0.24), r * 0.06, r * 0.05)
        sp_t = (self._phase * 0.6) % 1.0
        p.setBrush(QColor(251, 146, 60, int(230 * (1.0 - sp_t))))
        p.drawEllipse(QPointF(cx + r * 0.25 + math.sin(self._phase * 3) * r * 0.06, cy + r * 0.20 - sp_t * r * 0.55), r * 0.08, r * 0.08)

    def _draw_octopus(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # 5 curling bottom tentacles + rising sea bubbles
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#BE123C"))
        for i in range(-2, 3):
            tx = cx + i * r * 0.36
            sway = math.sin(self._phase * 2.6 + i * 0.9) * r * 0.14
            p.drawEllipse(QPointF(tx + sway, cy + r * 0.68), r * 0.22, r * 0.32)
        self._draw_3d_dome(p, cx, cy - r * 0.04, r * 0.94, r * 0.82, "#FDA4AF", "#FB7185", "#9F1239")
        for b_off, spd in ((-0.88, 0.0), (0.86, 0.52)):
            bt = (self._phase * 0.42 + spd) % 1.0
            p.setBrush(QColor(186, 230, 253, int(190 * (1.0 - bt))))
            p.drawEllipse(QPointF(cx + b_off * r, cy + r * 0.20 - bt * r * 1.15), r * 0.09, r * 0.09)
        self._draw_cheeks(p, cx, cy + r * 0.16, r * 0.38, r, "#FECDD3")
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.32, r * 0.19, color="#FFFFFF", pupil_hex="#0F172A")
        self._draw_smile(p, cx, cy + r * 0.22, r, "#4C0519", 0.16)

    def _draw_alien(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.setPen(QPen(QColor("#4ADE80"), max(1.5, r * 0.09), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for sign in (-1.0, 1.0):
            sw = math.sin(self._phase * 3.0 + sign * 1.1) * r * 0.14
            ax = cx + sign * r * 0.48 + sw
            ay = cy - r * 1.05 + math.cos(self._phase * 3.0 + sign) * r * 0.05
            p.drawLine(QPointF(cx + sign * r * 0.28, cy - r * 0.64), QPointF(ax, ay))
            glow_a = int(160 + 95 * math.sin(self._phase * 3.6 + sign))
            p.setBrush(QColor(190, 242, 100, glow_a))
            p.drawEllipse(QPointF(ax, ay - r * 0.03), r * 0.15, r * 0.15)
        self._draw_3d_dome(p, cx, cy + r * 0.04, r * 0.94, r * 0.82, "#86EFAC", "#22C55E", "#14532D")
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.35, r * 0.22, color="#FFFFFF", pupil_hex="#090D16")
        self._draw_smile(p, cx, cy + r * 0.34, r, "#052E16", 0.18)

    def _draw_panda(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        ear_bob = math.sin(self._phase * 2.4) * r * 0.04
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#18181B"))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * r * 0.66, cy - r * 0.64 + ear_bob * sign), r * 0.31, r * 0.31)
        self._draw_3d_dome(p, cx, cy + r * 0.06, r * 0.96, r * 0.86, "#FFFFFF", "#F1F5F9", "#94A3B8")
        # Tilted eye patches
        for sign in (-1.0, 1.0):
            p.save()
            p.translate(cx + sign * r * 0.34, cy - r * 0.02)
            p.rotate(sign * 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#18181B"))
            p.drawEllipse(QPointF(0, 0), r * 0.28, r * 0.22)
            p.restore()
        self._draw_cheeks(p, cx, cy + r * 0.20, r * 0.44, r, "#FDA4AF")
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.34, r * 0.15, color="#FFFFFF", pupil_hex="#09090B")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#18181B"))
        p.drawEllipse(QPointF(cx, cy + r * 0.18), r * 0.11, r * 0.07)
        self._draw_smile(p, cx, cy + r * 0.28, r, "#18181B", 0.18)
        # Munching green bamboo stalk bobbing gently at cheek
        p.save()
        p.translate(cx + r * 0.42, cy + r * 0.44)
        p.rotate(-24.0 + math.sin(self._phase * 3.4) * 11.0)
        p.setPen(QPen(QColor("#16A34A"), max(2.2, r * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(-r * 0.24, 0.0), QPointF(r * 0.36, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#4ADE80"))
        p.drawEllipse(QPointF(r * 0.28, -r * 0.11), r * 0.14, r * 0.06)
        p.restore()

    def _draw_axolotl(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # 3 fluttering coral gills on each side
        p.setPen(QPen(QColor("#FB7185"), max(1.8, r * 0.15), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for s in (-1.0, 1.0):
            for k in (-0.30, 0.0, 0.30):
                wave = math.sin(self._phase * 2.8 + k * 4.5) * r * 0.10
                p.drawLine(QPointF(cx + s * r * 0.72, cy + k * r * 0.45), QPointF(cx + s * r * 1.15, cy + k * r * 0.85 + wave))
        self._draw_3d_dome(p, cx, cy + r * 0.05, r * 0.90, r * 0.78, "#FCE7F3", "#F9A8D4", "#BE185D")
        self._draw_cheeks(p, cx, cy + r * 0.16, r * 0.40, r, "#F43F5E")
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.36, r * 0.16, color="#18181B", pupil_hex="#18181B")
        self._draw_smile(p, cx, cy + r * 0.20, r, "#831843", 0.28)

    def _draw_crystal(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        rot = self._phase * 0.55
        pts: list[QPointF] = []
        for i in range(6):
            a = rot + i * (math.tau / 6.0)
            scale = 1.06 if i % 2 == 0 else 0.78
            pts.append(QPointF(cx + math.cos(a) * r * scale, cy + math.sin(a) * r * scale))
        poly = QPolygonF(pts)
        grad = self._sphere_brush(cx, cy, r, "#F0FDFA", "#22D3EE", "#0E7490")
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 200), max(1.0, r * 0.06)))
        p.drawPolygon(poly)
        # Shimmering prismatic facet glint sweeping with rotation
        p.setPen(Qt.PenStyle.NoPen)
        gleam_a = int(55 + 45 * math.sin(self._phase * 2.6))
        p.setBrush(QColor(255, 255, 255, gleam_a))
        p.drawPolygon(QPolygonF([pts[0], pts[1], QPointF(cx, cy)]))
        p.drawPolygon(QPolygonF([pts[3], pts[4], QPointF(cx, cy)]))
        self._draw_eyes(p, cx, cy, r * 0.26, r * 0.15, color="#FFFFFF", pupil_hex="#083344")

    def _draw_slime(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        squish = math.sin(self._phase * 2.2) * 0.09 + self._audio_level * 0.14 + self._poke_energy * 0.20
        rx = r * (1.02 + squish)
        ry = r * (0.86 - squish * 0.55)
        self._draw_3d_dome(p, cx, cy + r * 0.12, rx, ry, "#BAE6FD", "#38BDF8", "#0369A1", rim_alpha=175)
        # Rising jelly bubbles drifting inside dome
        p.setPen(Qt.PenStyle.NoPen)
        b1 = (self._phase * 0.35) % 1.0
        b2 = (self._phase * 0.35 + 0.5) % 1.0
        p.setBrush(QColor(255, 255, 255, int(145 * (1.0 - b1 * 0.5))))
        p.drawEllipse(QPointF(cx + r * 0.42, cy + r * 0.38 - b1 * r * 0.65), r * 0.11, r * 0.11)
        p.setBrush(QColor(255, 255, 255, int(145 * (1.0 - b2 * 0.5))))
        p.drawEllipse(QPointF(cx - r * 0.48, cy + r * 0.42 - b2 * r * 0.62), r * 0.08, r * 0.08)
        self._draw_eyes(p, cx, cy + r * 0.08, r * 0.30, r * 0.17, color="#FFFFFF", pupil_hex="#0F172A")

    # ------------------------------------------------------------------ 20 Brand-New Tactical/Kawaii 3D Characters
    def _draw_frog(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Raised top eye bumps + pulsing throat pouch + playful pink tongue flick
        p.setPen(Qt.PenStyle.NoPen)
        for sign in (-1.0, 1.0):
            p.setBrush(self._sphere_brush(cx + sign * r * 0.46, cy - r * 0.46, r * 0.34, "#BEF264", "#4ADE80", "#15803D"))
            p.drawEllipse(QPointF(cx + sign * r * 0.46, cy - r * 0.46), r * 0.32, r * 0.32)
        self._draw_3d_dome(p, cx, cy + r * 0.10, r * 0.96, r * 0.72, "#A3E635", "#22C55E", "#166534")
        puff = 1.0 + max(0.0, math.sin(self._phase * 2.5)) * 0.24
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ECFCCB"))
        p.drawEllipse(QPointF(cx, cy + r * 0.38), r * 0.56 * puff, r * 0.28 * puff)
        self._draw_cheeks(p, cx, cy + r * 0.14, r * 0.42, r, "#FDA4AF")
        self._draw_eyes(p, cx, cy - r * 0.46, r * 0.46, r * 0.20, color="#FEF9C3", pupil_hex="#090D16")
        self._draw_smile(p, cx, cy + r * 0.10, r, "#14532D", 0.30)
        flick = max(0.0, math.sin(self._phase * 1.45) - 0.60) * 2.5
        if flick > 0.05:
            p.setPen(QPen(QColor("#F43F5E"), max(1.8, r * 0.10), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx, cy + r * 0.22), QPointF(cx + r * 0.12 * flick, cy + r * (0.22 + 0.34 * flick)))

    def _draw_penguin(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        p.translate(cx, cy)
        p.rotate(math.sin(self._phase * 2.5) * 7.5)
        p.translate(-cx, -cy)
        flap = math.sin(self._phase * 3.8) * 18.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1E293B"))
        for sign in (-1.0, 1.0):
            p.save()
            p.translate(cx + sign * r * 0.80, cy + r * 0.20)
            p.rotate(sign * flap)
            p.drawEllipse(QPointF(sign * r * 0.12, r * 0.08), r * 0.22, r * 0.38)
            p.restore()
        self._draw_3d_dome(p, cx, cy + r * 0.04, r * 0.90, r * 0.94, "#64748B", "#334155", "#0F172A")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F8FAFC"))
        p.drawEllipse(QPointF(cx - r * 0.24, cy + r * 0.02), r * 0.42, r * 0.56)
        p.drawEllipse(QPointF(cx + r * 0.24, cy + r * 0.02), r * 0.42, r * 0.56)
        p.drawEllipse(QPointF(cx, cy + r * 0.42), r * 0.64, r * 0.46)
        self._draw_cheeks(p, cx, cy + r * 0.14, r * 0.38, r, "#FB7185")
        self._draw_eyes(p, cx, cy - r * 0.12, r * 0.30, r * 0.16, color="#1E293B", pupil_hex="#020617")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F59E0B"))
        p.drawPolygon(QPolygonF([QPointF(cx - r * 0.18, cy + r * 0.08), QPointF(cx + r * 0.18, cy + r * 0.08), QPointF(cx, cy + r * 0.32)]))
        p.restore()

    def _draw_bee(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        cx += math.sin(self._phase * 3.8) * r * 0.07
        wing_f = math.sin(self._phase * 7.5) * r * 0.14
        p.setPen(QPen(QColor(255, 255, 255, 190), max(0.9, r * 0.05)))
        p.setBrush(QColor(224, 242, 254, 155))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * r * 0.66, cy - r * 0.44 + wing_f), r * 0.42, r * 0.28)
        # Floating golden pollen dots
        p.setPen(Qt.PenStyle.NoPen)
        pt_t = (self._phase * 0.6) % 1.0
        p.setBrush(QColor(253, 224, 71, int(220 * (1.0 - pt_t))))
        p.drawEllipse(QPointF(cx - r * 0.92 - pt_t * r * 0.3, cy + r * 0.2 + math.sin(self._phase * 3) * r * 0.1), r * 0.08, r * 0.08)
        self._draw_3d_dome(p, cx, cy + r * 0.08, r * 0.90, r * 0.80, "#FEF08A", "#FBBF24", "#B45309")
        p.setPen(QPen(QColor("#1E1B18"), max(2.2, r * 0.20), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - r * 0.62, cy + r * 0.38), QPointF(cx + r * 0.62, cy + r * 0.38))
        p.drawLine(QPointF(cx - r * 0.44, cy + r * 0.62), QPointF(cx + r * 0.44, cy + r * 0.62))
        self._draw_cheeks(p, cx, cy + r * 0.08, r * 0.38, r, "#FB7185")
        self._draw_eyes(p, cx, cy - r * 0.12, r * 0.30, r * 0.17, color="#FFFFFF", pupil_hex="#1E1B18")

    def _draw_bunny(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        for sign in (-1.0, 1.0):
            wiggle = math.sin(self._phase * 2.4 + (0.0 if sign < 0 else 1.4)) * 9.0
            p.save()
            p.translate(cx + sign * r * 0.44, cy - r * 0.56)
            p.rotate(sign * (12.0 + wiggle))
            p.setBrush(QColor("#F8FAFC"))
            p.setPen(QPen(QColor(255, 255, 255, 160), max(0.8, r * 0.05)))
            p.drawEllipse(QPointF(0, -r * 0.36), r * 0.22, r * 0.52)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FBCFE8"))
            p.drawEllipse(QPointF(0, -r * 0.34), r * 0.12, r * 0.36)
            p.restore()
        self._draw_3d_dome(p, cx, cy + r * 0.12, r * 0.90, r * 0.78, "#FFFFFF", "#F8FAFC", "#CBD5E1")
        self._draw_cheeks(p, cx, cy + r * 0.22, r * 0.40, r, "#F9A8D4")
        self._draw_eyes(p, cx, cy + r * 0.02, r * 0.32, r * 0.17, color="#FFFFFF", pupil_hex="#1E293B")
        nose_bob = math.sin(self._phase * 4.8) * r * 0.03
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F472B6"))
        p.drawEllipse(QPointF(cx, cy + r * 0.20 + nose_bob), r * 0.09, r * 0.06)

    def _draw_capybara(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Bobbing mandarin on head + floating spa zen steam rings
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#78350F"))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * r * 0.64, cy - r * 0.56), r * 0.20, r * 0.18)
        self._draw_3d_dome(p, cx, cy + r * 0.12, r * 0.98, r * 0.78, "#D6A574", "#A16207", "#451A03")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#78350F"))
        p.drawEllipse(QPointF(cx, cy + r * 0.32), r * 0.44, r * 0.28)
        mand_y = cy - r * 0.78 + math.sin(self._phase * 2.0) * r * 0.05
        self._draw_3d_dome(p, cx, mand_y, r * 0.26, r * 0.22, "#FDBA74", "#F97316", "#C2410C")
        p.setBrush(QColor("#22C55E"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx + r * 0.08 + math.sin(self._phase * 2.0) * r * 0.03, mand_y - r * 0.24), r * 0.11, r * 0.05)
        st_t = (self._phase * 0.4) % 1.0
        p.setPen(QPen(QColor(255, 255, 255, int(130 * (1.0 - st_t))), max(1.0, r * 0.05)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, mand_y - r * 0.35 - st_t * r * 0.28), r * (0.20 + 0.15 * st_t), r * 0.08)
        # Chill zen line eyes
        p.setPen(QPen(QColor("#1C1917"), max(1.6, r * 0.09), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for sign in (-1.0, 1.0):
            ex = cx + sign * r * 0.38
            p.drawLine(QPointF(ex - r * 0.14, cy - r * 0.04), QPointF(ex + r * 0.14, cy - r * 0.04))
        self._draw_smile(p, cx, cy + r * 0.34, r, "#1C1917", 0.18)

    def _draw_duck(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Swaying curl + flapping yellow wings + quacking beak
        flap = math.sin(self._phase * 4.2) * 18.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EAB308"))
        for sign in (-1.0, 1.0):
            p.save()
            p.translate(cx + sign * r * 0.84, cy + r * 0.15)
            p.rotate(sign * flap)
            p.drawEllipse(QPointF(sign * r * 0.14, 0.0), r * 0.24, r * 0.15)
            p.restore()
        curl_dx = math.sin(self._phase * 2.5) * r * 0.06
        p.setPen(QPen(QColor("#FACC15"), max(2.0, r * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - r * 0.18 + curl_dx, cy - r * 1.05, r * 0.36, r * 0.36), 30 * 16, 200 * 16)
        self._draw_3d_dome(p, cx, cy + r * 0.06, r * 0.92, r * 0.84, "#FEF9C3", "#FACC15", "#CA8A04")
        self._draw_cheeks(p, cx, cy + r * 0.14, r * 0.40, r, "#FB7185")
        self._draw_eyes(p, cx, cy - r * 0.10, r * 0.32, r * 0.17, color="#FFFFFF", pupil_hex="#0F172A")
        quack = max(0.0, math.sin(self._phase * 4.0)) * r * 0.06
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F97316"))
        p.drawEllipse(QPointF(cx, cy + r * 0.20 - quack * 0.3), r * 0.38, r * 0.15)
        p.setBrush(QColor("#EA580C"))
        p.drawEllipse(QPointF(cx, cy + r * 0.25 + quack), r * 0.32, r * 0.11)

    def _draw_koala(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        ear_w = math.sin(self._phase * 2.2) * r * 0.04
        for sign in (-1.0, 1.0):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#94A3B8"))
            p.drawEllipse(QPointF(cx + sign * r * 0.84, cy - r * 0.28 + ear_w * sign), r * 0.42, r * 0.42)
            p.setBrush(QColor("#F1F5F9"))
            p.drawEllipse(QPointF(cx + sign * r * 0.80, cy - r * 0.24 + ear_w * sign), r * 0.25, r * 0.25)
        self._draw_3d_dome(p, cx, cy + r * 0.08, r * 0.86, r * 0.78, "#E2E8F0", "#94A3B8", "#475569")
        self._draw_cheeks(p, cx, cy + r * 0.22, r * 0.40, r, "#FDA4AF")
        self._draw_eyes(p, cx, cy - r * 0.06, r * 0.34, r * 0.16, color="#FFFFFF", pupil_hex="#0F172A")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1E293B"))
        p.drawEllipse(QPointF(cx, cy + r * 0.18), r * 0.18, r * 0.26)
        # Swaying eucalyptus leaf snack near chin
        p.save()
        p.translate(cx + r * 0.24, cy + r * 0.50)
        p.rotate(math.sin(self._phase * 3.1) * 14.0)
        p.setBrush(QColor("#22C55E"))
        p.drawEllipse(QPointF(r * 0.16, 0.0), r * 0.20, r * 0.09)
        p.restore()

    def _draw_raccoon(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        p.translate(cx, cy)
        p.rotate(math.sin(self._phase * 1.8) * 6.0)
        p.translate(-cx, -cy)
        self._draw_eared_mammal(p, cx, cy, r, "#CBD5E1", "#475569", "#F8FAFC", cheek="#94A3B8", muzzle_patch=True)
        # Iconic dark bandit mask across eyes
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("#1E293B", 230))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * r * 0.34, cy - r * 0.02), r * 0.30, r * 0.22)
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.34, r * 0.16, color="#FFFFFF", pupil_hex="#0F172A")
        # Twinkling stolen star glint
        gl = 0.5 + 0.5 * math.sin(self._phase * 3.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(250, 204, 21, int(230 * gl)))
        p.drawEllipse(QPointF(cx - r * 0.94, cy - r * 0.52), r * 0.11 * (0.6 + 0.5 * gl), r * 0.11 * (0.6 + 0.5 * gl))
        p.restore()

    def _draw_unicorn(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Spiraled glowing golden horn + pastel ears + orbiting star spark
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E9D5FF"))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * r * 0.52, cy - r * 0.64), r * 0.18, r * 0.30)
        horn_g = QLinearGradient(cx, cy - r * 1.24, cx, cy - r * 0.56)
        horn_g.setColorAt(0.0, QColor("#FEF08A"))
        horn_g.setColorAt(0.5, QColor("#F472B6"))
        horn_g.setColorAt(1.0, QColor("#38BDF8"))
        p.setBrush(horn_g)
        p.drawPolygon(QPolygonF([QPointF(cx - r * 0.18, cy - r * 0.58), QPointF(cx, cy - r * 1.26), QPointF(cx + r * 0.18, cy - r * 0.58)]))
        sp_a = self._phase * 2.1
        p.setBrush(QColor(254, 240, 138, 220))
        p.drawEllipse(QPointF(cx + math.cos(sp_a) * r * 0.45, cy - r * 1.05 + math.sin(sp_a) * r * 0.18), r * 0.09, r * 0.09)
        self._draw_3d_dome(p, cx, cy + r * 0.10, r * 0.90, r * 0.80, "#FFFFFF", "#F3E8FF", "#A855F7")
        self._draw_cheeks(p, cx, cy + r * 0.22, r * 0.40, r, "#F9A8D4")
        self._draw_eyes(p, cx, cy + r * 0.02, r * 0.32, r * 0.18, color="#FFFFFF", pupil_hex="#4C1D95")

    def _draw_red_panda(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        self._draw_eared_mammal(
            p, cx, cy, r, "#EA580C", "#9A3412", "#FFF7ED", cheek="#FDBA74", brows=True, muzzle_patch=True,
            tail_colors=("#C2410C", "#7C2D12"),
        )
        # Dark chestnut vertical eye teardrops
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#431407"))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * r * 0.34, cy + r * 0.10), r * 0.16, r * 0.24)
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.34, r * 0.16, color="#FFFFFF", pupil_hex="#18181B")

    def _draw_whale(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Bubbly erupting water spout fountain arcs + droplets
        spout_h = math.sin(self._phase * 2.8) * r * 0.10
        p.setPen(QPen(QColor("#BAE6FD"), max(1.6, r * 0.09), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for sign in (-1.0, 1.0):
            p.drawArc(QRectF(cx + (sign * 0.02 - 0.28) * r, cy - r * 1.12 - spout_h, r * 0.56, r * 0.56), 0 if sign > 0 else 90 * 16, 90 * 16)
        dt = (self._phase * 0.55) % 1.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(224, 242, 254, int(230 * (1.0 - dt))))
        for sign in (-1.0, 1.0):
            p.drawEllipse(QPointF(cx + sign * (0.14 + dt * 0.36) * r, cy - r * (0.90 + math.sin(dt * math.pi) * 0.35)), r * 0.075, r * 0.075)
        self._draw_3d_dome(p, cx, cy + r * 0.10, r * 1.00, r * 0.78, "#7DD3FC", "#0284C7", "#0C4A6E")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F0F9FF"))
        p.drawEllipse(QPointF(cx, cy + r * 0.52), r * 0.74, r * 0.34)
        self._draw_cheeks(p, cx, cy + r * 0.20, r * 0.42, r, "#F9A8D4")
        self._draw_eyes(p, cx, cy + r * 0.02, r * 0.38, r * 0.16, color="#FFFFFF", pupil_hex="#082F49")
        self._draw_smile(p, cx, cy + r * 0.22, r, "#082F49", 0.22)

    def _draw_shark(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Side-to-side patrolling swim glide + tilting dorsal fin
        swim_x = math.sin(self._phase * 2.0) * r * 0.09
        cx += swim_x
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#475569"))
        fin_lean = math.sin(self._phase * 2.0) * r * 0.10
        p.drawPolygon(QPolygonF([QPointF(cx - r * 0.22, cy - r * 0.62), QPointF(cx + r * 0.08 + fin_lean, cy - r * 1.18), QPointF(cx + r * 0.30, cy - r * 0.58)]))
        self._draw_3d_dome(p, cx, cy + r * 0.08, r * 0.94, r * 0.82, "#CBD5E1", "#64748B", "#1E293B")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QPointF(cx, cy + r * 0.42), r * 0.68, r * 0.40)
        p.setBrush(QColor("#0F172A"))
        p.drawArc(QRectF(cx - r * 0.42, cy + r * 0.10, r * 0.84, r * 0.44), 190 * 16, 160 * 16)
        self._draw_eyes(p, cx, cy - r * 0.06, r * 0.36, r * 0.17, color="#FFFFFF", pupil_hex="#020617")

    def _draw_mushroom(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Stem + independent springy red polka-dot cap bounce + rising forest spore motes
        self._draw_3d_dome(p, cx, cy + r * 0.44, r * 0.56, r * 0.48, "#FFFBEB", "#FDE68A", "#D97706")
        cap_dy = math.sin(self._phase * 2.6) * r * 0.07
        self._draw_3d_dome(p, cx, cy - r * 0.16 + cap_dy, r * 1.02, r * 0.66, "#FCA5A5", "#EF4444", "#991B1B")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 225))
        p.drawEllipse(QPointF(cx - r * 0.46, cy - r * 0.24 + cap_dy), r * 0.18, r * 0.14)
        p.drawEllipse(QPointF(cx + r * 0.38, cy - r * 0.34 + cap_dy), r * 0.14, r * 0.12)
        p.drawEllipse(QPointF(cx + r * 0.02, cy - r * 0.56 + cap_dy), r * 0.15, r * 0.10)
        sp_t = (self._phase * 0.45) % 1.0
        p.setBrush(QColor(253, 224, 71, int(200 * (1.0 - sp_t))))
        p.drawEllipse(QPointF(cx - r * 0.82, cy - r * 0.2 - sp_t * r * 0.8), r * 0.07, r * 0.07)
        self._draw_cheeks(p, cx, cy + r * 0.52, r * 0.28, r, "#FDA4AF")
        self._draw_eyes(p, cx, cy + r * 0.38, r * 0.22, r * 0.13, color="#18181B", pupil_hex="#18181B")

    def _draw_star(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Pulsing radial aura + joyful tilt spin
        aura = 1.08 + 0.14 * math.sin(self._phase * 2.8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(254, 240, 138, 48))
        p.drawEllipse(QPointF(cx, cy), r * aura, r * aura)
        pts: list[QPointF] = []
        tilt = math.sin(self._phase * 1.1) * 0.16
        for i in range(10):
            a = -math.pi * 0.5 + tilt + i * (math.tau / 10.0)
            rad = r * (1.06 if i % 2 == 0 else 0.52)
            pts.append(QPointF(cx + math.cos(a) * rad, cy + math.sin(a) * rad))
        p.setBrush(self._sphere_brush(cx, cy, r, "#FEF9C3", "#FACC15", "#B45309"))
        p.setPen(QPen(QColor("#FFFBEB"), max(1.0, r * 0.06), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(QPolygonF(pts))
        self._draw_cheeks(p, cx, cy + r * 0.14, r * 0.30, r, "#FB7185")
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.24, r * 0.14, color="#1E293B", pupil_hex="#0F172A")

    def _draw_moon(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        p.translate(cx, cy)
        p.rotate(math.sin(self._phase * 1.2) * 7.5)
        p.translate(-cx, -cy)
        body = QPainterPath()
        body.addEllipse(QPointF(cx, cy), r * 0.92, r * 0.92)
        cut = QPainterPath()
        cut.addEllipse(QPointF(cx + r * 0.42, cy - r * 0.26), r * 0.72, r * 0.72)
        crescent = body.subtracted(cut)
        p.setBrush(self._sphere_brush(cx - r * 0.2, cy, r, "#FEF9C3", "#FDE047", "#CA8A04"))
        p.setPen(QPen(QColor(255, 255, 255, 160), max(1.0, r * 0.05)))
        p.drawPath(crescent)
        # Orbiting twinkling starlet
        st_y = cy - r * 0.46 + math.sin(self._phase * 2.0) * r * 0.12
        tw_s = 0.75 + 0.35 * math.sin(self._phase * 3.8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFBEB"))
        p.drawEllipse(QPointF(cx + r * 0.56, st_y), r * 0.14 * tw_s, r * 0.14 * tw_s)
        self._draw_eyes(p, cx - r * 0.30, cy + r * 0.14, r * 0.20, r * 0.13, color="#1E293B", pupil_hex="#0F172A", happy=True)
        p.restore()

    def _draw_cloud(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Peeking sunny orb + breathing drifting cloud puffs
        sun_peek = math.sin(self._phase * 1.4) * r * 0.08
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FACC15"))
        p.drawEllipse(QPointF(cx - r * 0.48 + sun_peek, cy - r * 0.52), r * 0.30, r * 0.30)
        col_g = self._sphere_brush(cx, cy - r * 0.2, r * 1.2, "#FFFFFF", "#E0F2FE", "#93C5FD")
        p.setBrush(col_g)
        for i, (ox, oy, rad) in enumerate(((-0.52, 0.16, 0.48), (0.52, 0.16, 0.46), (-0.18, -0.22, 0.56), (0.26, -0.14, 0.48), (0.0, 0.24, 0.54))):
            dx = math.sin(self._phase * 1.7 + i * 1.1) * 0.04
            p.drawEllipse(QPointF(cx + (ox + dx) * r, cy + oy * r), rad * r, rad * r * 0.90)
        self._draw_cheeks(p, cx, cy + r * 0.24, r * 0.38, r, "#FDA4AF")
        self._draw_eyes(p, cx, cy + r * 0.06, r * 0.28, r * 0.16, color="#1E293B", pupil_hex="#0F172A")

    def _draw_flame(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        flicker = math.sin(self._phase * 3.8) * r * 0.11
        outer = QPainterPath()
        outer.moveTo(cx + flicker, cy - r * 1.10)
        outer.cubicTo(cx + r * 1.12, cy - r * 0.15, cx + r * 0.92, cy + r * 0.96, cx, cy + r * 0.92)
        outer.cubicTo(cx - r * 0.92, cy + r * 0.96, cx - r * 1.12, cy - r * 0.15, cx + flicker, cy - r * 1.10)
        fg = QLinearGradient(cx, cy - r, cx, cy + r)
        fg.setColorAt(0.0, QColor("#FDE047"))
        fg.setColorAt(0.45, QColor("#FB923C"))
        fg.setColorAt(1.0, QColor("#DC2626"))
        p.setBrush(fg)
        p.setPen(QPen(QColor("#FEF08A"), max(1.0, r * 0.05)))
        p.drawPath(outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FEF9C3"))
        p.drawEllipse(QPointF(cx, cy + r * 0.32), r * 0.48, r * 0.44)
        for s_off, spd in ((-0.45, 0.0), (0.50, 0.48)):
            et = (self._phase * 0.65 + spd) % 1.0
            p.setBrush(QColor(253, 224, 71, int(235 * (1.0 - et))))
            p.drawEllipse(QPointF(cx + s_off * r + math.sin(self._phase * 3 + spd) * r * 0.06, cy - r * 0.3 - et * r * 0.8), r * 0.075, r * 0.075)
        self._draw_eyes(p, cx, cy + r * 0.22, r * 0.26, r * 0.15, color="#FFFFFF", pupil_hex="#7C2D12")

    def _draw_boba(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Wiggling boba straw + bouncing tapioca pearls
        st_dx = math.sin(self._phase * 2.6) * r * 0.07
        p.setPen(QPen(QColor("#F472B6"), max(2.6, r * 0.22), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx + r * 0.18, cy - r * 0.45), QPointF(cx + r * 0.44 + st_dx, cy - r * 1.12))
        cup_g = self._sphere_brush(cx, cy + r * 0.1, r, "#F5D0FE", "#D8B4FE", "#7E22CE")
        p.setBrush(cup_g)
        p.setPen(QPen(QColor(255, 255, 255, 190), max(1.0, r * 0.06)))
        p.drawRoundedRect(QRectF(cx - r * 0.72, cy - r * 0.56, r * 1.44, r * 1.44), r * 0.36, r * 0.36)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(cx - r * 0.80, cy - r * 0.66, r * 1.60, r * 0.26), r * 0.13, r * 0.13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1E1B18"))
        for idx, (px, py) in enumerate(((-0.36, 0.58), (0.0, 0.64), (0.36, 0.56), (-0.18, 0.72), (0.20, 0.72))):
            bob = math.sin(self._phase * 3.0 + idx * 1.3) * r * 0.06
            p.drawEllipse(QPointF(cx + px * r, cy + py * r + bob), r * 0.11, r * 0.11)
        self._draw_cheeks(p, cx, cy + r * 0.14, r * 0.32, r, "#F472B6")
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.26, r * 0.15, color="#FFFFFF", pupil_hex="#1E1B4B")

    def _draw_dino(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Wagging green dino tail + nodding dorsal crest spikes
        wag = math.sin(self._phase * 3.0) * r * 0.14
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#059669"))
        p.drawPolygon(QPolygonF([
            QPointF(cx - r * 0.72, cy + r * 0.25),
            QPointF(cx - r * 1.20, cy + r * 0.05 + wag),
            QPointF(cx - r * 0.72, cy + r * 0.58),
        ]))
        p.setBrush(QColor("#FB923C"))
        for k, (ox, oy) in enumerate(((-0.52, -0.62), (-0.10, -0.86), (0.36, -0.74))):
            nod = math.sin(self._phase * 3.2 + k) * r * 0.04
            p.drawEllipse(QPointF(cx + ox * r, cy + oy * r + nod), r * 0.20, r * 0.20)
        self._draw_3d_dome(p, cx, cy + r * 0.06, r * 0.94, r * 0.82, "#A7F3D0", "#10B981", "#065F46")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ECFDF5"))
        p.drawEllipse(QPointF(cx, cy + r * 0.44), r * 0.62, r * 0.34)
        self._draw_cheeks(p, cx, cy + r * 0.18, r * 0.40, r, "#FB7185")
        self._draw_eyes(p, cx, cy - r * 0.06, r * 0.32, r * 0.18, color="#FFFFFF", pupil_hex="#022C22")
        self._draw_smile(p, cx, cy + r * 0.20, r, "#064E3B", 0.22)

    def _draw_phoenix(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Blazing fire wings flapping + crown flame plumes
        wflap = math.sin(self._phase * 3.4) * 16.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F97316"))
        for sign in (-1.0, 1.0):
            p.save()
            p.translate(cx + sign * r * 0.72, cy + r * 0.08)
            p.rotate(sign * wflap)
            p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(sign * r * 0.56, -r * 0.42), QPointF(sign * r * 0.38, r * 0.30)]))
            p.restore()
        p.setBrush(QColor("#F59E0B"))
        for ang, dx in ((-0.25, -0.36), (0.0, 0.0), (0.25, 0.36)):
            flick = math.sin(self._phase * 3.8 + ang * 4) * r * 0.07
            plume = QPolygonF([
                QPointF(cx + dx * r * 0.4, cy - r * 0.56),
                QPointF(cx + dx * r * 1.15 + flick, cy - r * 1.18 + abs(ang) * r * 0.4),
                QPointF(cx + dx * r * 0.9, cy - r * 0.34),
            ])
            p.drawPolygon(plume)
        self._draw_3d_dome(p, cx, cy + r * 0.08, r * 0.92, r * 0.84, "#FDBA74", "#EF4444", "#7F1D1D")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FEF08A"))
        p.drawEllipse(QPointF(cx, cy + r * 0.28), r * 0.58, r * 0.42)
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.32, r * 0.18, color="#FFFBEB", pupil_hex="#450A0A")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F59E0B"))
        p.drawPolygon(QPolygonF([QPointF(cx - r * 0.14, cy + r * 0.12), QPointF(cx + r * 0.14, cy + r * 0.12), QPointF(cx, cy + r * 0.32)]))

    def _draw_borscht(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Steam wisps rising smoothly above hot borscht
        for sx_off, spd in ((-0.38, 0.0), (0.0, 1.4), (0.36, 2.7)):
            wave = math.sin(self._phase * 2.4 + spd) * r * 0.09
            st_col = QColor(255, 248, 245, 130)
            p.setPen(QPen(st_col, max(1.3, r * 0.075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            steam = QPainterPath()
            bx = cx + sx_off * r
            steam.moveTo(bx, cy - r * 0.50)
            steam.cubicTo(bx + wave, cy - r * 0.72, bx - wave, cy - r * 0.90, bx + wave * 0.6, cy - r * 1.12)
            p.drawPath(steam)

        # Porcelain white soup bowl lower body
        bowl_g = self._sphere_brush(cx, cy + r * 0.25, r * 1.05, "#FFFFFF", "#F1F5F9", "#94A3B8")
        bowl = QPainterPath()
        bowl.moveTo(cx - r * 0.96, cy - r * 0.22)
        bowl.cubicTo(cx - r * 0.96, cy + r * 0.84, cx - r * 0.45, cy + r * 0.92, cx, cy + r * 0.92)
        bowl.cubicTo(cx + r * 0.45, cy + r * 0.92, cx + r * 0.96, cy + r * 0.84, cx + r * 0.96, cy - r * 0.22)
        bowl.closeSubpath()
        p.setBrush(bowl_g)
        p.setPen(QPen(QColor(255, 255, 255, 210), max(1.0, r * 0.06)))
        p.drawPath(bowl)

        # Rim + rich crimson borscht broth
        p.setBrush(QColor("#FFFDF9"))
        p.setPen(QPen(QColor("#CBD5E1"), max(1.0, r * 0.04)))
        p.drawEllipse(QPointF(cx, cy - r * 0.22), r * 0.98, r * 0.34)

        stir_a = self._phase * 2.4
        swirl_cx = cx + math.cos(stir_a) * r * 0.04
        swirl_cy = cy - r * 0.21 + math.sin(stir_a) * r * 0.015
        broth_g = QRadialGradient(QPointF(swirl_cx, swirl_cy), r * 0.88)
        broth_g.setColorAt(0.0, QColor("#EF4444"))
        broth_g.setColorAt(0.55, QColor("#DC2626"))
        broth_g.setColorAt(1.0, QColor("#7F1D1D"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(broth_g)
        p.drawEllipse(QPointF(cx, cy - r * 0.21), r * 0.88, r * 0.27)

        # Swirling cream vortex + orbiting beet/potato/dill chunks stirred by the spoon
        p.setPen(QPen(QColor(255, 253, 248, 215), max(1.3, r * 0.065), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        deg0 = int(math.degrees(-stir_a) * 16)
        p.drawArc(QRectF(cx - r * 0.44, cy - r * 0.34, r * 0.88, r * 0.26), deg0, 140 * 16)
        p.drawArc(QRectF(cx - r * 0.26, cy - r * 0.29, r * 0.52, r * 0.16), deg0 + 180 * 16, 130 * 16)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFDF8"))
        p.drawEllipse(QPointF(cx + math.cos(stir_a) * r * 0.08, cy - r * 0.21 + math.sin(stir_a) * r * 0.03), r * 0.18, r * 0.085)
        for k, (rad_f, col_hex) in enumerate(((0.56, "#4ADE80"), (0.42, "#FB923C"), (0.64, "#FDE047"), (0.34, "#4ADE80"), (0.50, "#991B1B"))):
            ia = stir_a + k * (math.tau / 5.0)
            p.setBrush(QColor(col_hex))
            p.drawEllipse(
                QPointF(cx + math.cos(ia) * r * rad_f, cy - r * 0.21 + math.sin(ia) * r * 0.17),
                r * 0.055,
                r * 0.038,
            )

        # Orbiting silver soup spoon actively circling and stirring the borscht
        sp_bx = cx + math.cos(stir_a) * r * 0.46
        sp_by = cy - r * 0.20 + math.sin(stir_a) * r * 0.11
        sp_hx = cx + math.cos(stir_a) * r * 0.72 + r * 0.14
        sp_hy = cy - r * 0.92 + math.sin(stir_a) * r * 0.10
        # Submerged spoon scoop rim peeking above broth
        p.setBrush(QColor("#CBD5E1"))
        p.setPen(QPen(QColor("#FFFFFF"), max(0.8, r * 0.035)))
        p.drawEllipse(QPointF(sp_bx, sp_by), r * 0.14, r * 0.07)
        # Rotating metallic silver handle
        p.setPen(QPen(QColor("#94A3B8"), max(2.8, r * 0.15), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(sp_bx, sp_by - r * 0.02), QPointF(sp_hx, sp_hy))
        p.setPen(QPen(QColor("#F8FAFC"), max(1.4, r * 0.07), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(sp_bx, sp_by - r * 0.02), QPointF(sp_hx, sp_hy))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E2E8F0"))
        p.drawEllipse(QPointF(sp_hx, sp_hy), r * 0.095, r * 0.095)

        # Cozy eyes, rosy cheeks & warm smile on the bowl front
        self._draw_cheeks(p, cx, cy + r * 0.42, r * 0.40, r, "#FDA4AF")
        self._draw_eyes(p, cx, cy + r * 0.26, r * 0.30, r * 0.14, color="#1E293B", pupil_hex="#0F172A")
        self._draw_smile(p, cx, cy + r * 0.42, r, "#475569", 0.20)

    def _draw_purple_star(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        pts: list[QPointF] = []
        tilt = math.sin(self._phase * 1.15) * 0.16
        for i in range(10):
            a = -math.pi * 0.5 + tilt + i * (math.tau / 10.0)
            rad = r * (1.08 if i % 2 == 0 else 0.54)
            pts.append(QPointF(cx + math.cos(a) * rad, cy + math.sin(a) * rad))
        p.setBrush(self._sphere_brush(cx, cy, r * 1.05, "#F5D0FE", "#A855F7", "#581C87"))
        p.setPen(QPen(QColor("#FAE8FF"), max(1.1, r * 0.065), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(QPolygonF(pts))
        # Two orbiting neon-lavender starlight sparks
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(245, 208, 254, 225))
        for off in (0.0, math.pi):
            sp_a = self._phase * 1.7 + off
            p.drawEllipse(QPointF(cx + math.cos(sp_a) * r * 1.06, cy + math.sin(sp_a) * r * 0.85), r * 0.09, r * 0.09)
        self._draw_cheeks(p, cx, cy + r * 0.16, r * 0.30, r, "#F472B6")
        self._draw_eyes(p, cx, cy - r * 0.01, r * 0.24, r * 0.15, color="#FFFFFF", pupil_hex="#2E1065")
        self._draw_smile(p, cx, cy + r * 0.16, r, "#2E1065", 0.16)

    def _draw_kamaz(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Heavy orange-amber mining KAMAZ / BelAZ dump truck with diesel rumble, tilting dump bed, pulsing headlights & spinning wheel spokes
        rumble_y = math.sin(self._phase * 11.0) * r * 0.018
        cy += rumble_y
        bed_lift = abs(math.sin(self._phase * 1.9)) * r * 0.13 * (0.4 + self._audio_level)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#64748B"))
        p.drawEllipse(QPointF(cx - r * 0.24, cy - r * 0.42 - bed_lift * 0.5), r * 0.56, r * 0.26)
        bed = QPolygonF([
            QPointF(cx - r * 0.98, cy - r * 0.34 - bed_lift),
            QPointF(cx + r * 0.48, cy - r * 0.48 - bed_lift * 0.4),
            QPointF(cx + r * 0.36, cy + r * 0.22),
            QPointF(cx - r * 0.88, cy + r * 0.22),
        ])
        bed_g = QLinearGradient(cx - r, cy - r * 0.5, cx + r * 0.4, cy + r * 0.25)
        bed_g.setColorAt(0.0, QColor("#FDE047"))
        bed_g.setColorAt(0.55, QColor("#F59E0B"))
        bed_g.setColorAt(1.0, QColor("#B45309"))
        p.setBrush(bed_g)
        p.setPen(QPen(QColor("#FEF08A"), max(1.0, r * 0.045), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolygon(bed)
        p.drawRoundedRect(QRectF(cx - r * 0.04, cy - r * 0.54, r * 1.00, r * 0.22), r * 0.08, r * 0.08)

        cab_g = QLinearGradient(cx, cy - r * 0.35, cx + r * 0.95, cy + r * 0.55)
        cab_g.setColorAt(0.0, QColor("#FDBA74"))
        cab_g.setColorAt(0.45, QColor("#EA580C"))
        cab_g.setColorAt(1.0, QColor("#9A3412"))
        p.setBrush(cab_g)
        p.setPen(QPen(QColor(255, 255, 255, 165), max(1.0, r * 0.045)))
        p.drawRoundedRect(QRectF(cx - r * 0.10, cy - r * 0.36, r * 1.04, r * 0.84), r * 0.20, r * 0.20)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1E293B"))
        p.drawRoundedRect(QRectF(cx + r * 0.02, cy - r * 0.26, r * 0.80, r * 0.38), r * 0.11, r * 0.11)
        self._draw_eyes(p, cx + r * 0.42, cy - r * 0.07, r * 0.22, r * 0.13, color="#FFFFFF", pupil_hex="#020617")

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0F172A"))
        p.drawRoundedRect(QRectF(cx + r * 0.14, cy + r * 0.18, r * 0.56, r * 0.22), r * 0.07, r * 0.07)
        hl_a = int(190 + 65 * math.sin(self._phase * 4.5))
        p.setBrush(QColor(254, 240, 138, hl_a))
        p.drawEllipse(QPointF(cx + r * 0.02, cy + r * 0.28), r * 0.10, r * 0.10)
        p.drawEllipse(QPointF(cx + r * 0.82, cy + r * 0.28), r * 0.10, r * 0.10)

        # Massive BelAZ mining tires with spinning lug spokes
        rot_a = self._phase * 3.2
        for wx in (-0.48, 0.50):
            wcx, wcy = cx + wx * r, cy + r * 0.58 - rumble_y * 0.5
            p.setPen(QPen(QColor("#334155"), max(1.2, r * 0.06)))
            p.setBrush(QColor("#0F172A"))
            p.drawEllipse(QPointF(wcx, wcy), r * 0.34, r * 0.34)
            p.setPen(QPen(QColor("#94A3B8"), max(1.0, r * 0.045)))
            for spk in range(3):
                sa = rot_a + spk * (math.pi / 3.0)
                p.drawLine(QPointF(wcx - math.cos(sa) * r * 0.20, wcy - math.sin(sa) * r * 0.20), QPointF(wcx + math.cos(sa) * r * 0.20, wcy + math.sin(sa) * r * 0.20))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#CBD5E1"))
            p.drawEllipse(QPointF(wcx, wcy), r * 0.12, r * 0.12)

    def _draw_meimei(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        # Drifting gentle winter snowflakes + twitching fluffy ears + waving front paw
        sf_t1 = (self._phase * 0.4) % 1.0
        sf_t2 = (self._phase * 0.4 + 0.5) % 1.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(210 * (1.0 - sf_t1))))
        p.drawEllipse(QPointF(cx - r * 0.92 + math.sin(self._phase * 2) * r * 0.08, cy - r * 0.9 + sf_t1 * r * 1.4), r * 0.07, r * 0.07)
        p.setBrush(QColor(255, 255, 255, int(210 * (1.0 - sf_t2))))
        p.drawEllipse(QPointF(cx + r * 0.92 + math.cos(self._phase * 2) * r * 0.08, cy - r * 0.8 + sf_t2 * r * 1.4), r * 0.065, r * 0.065)

        p.setPen(QPen(QColor("#94A3B8"), max(1.0, r * 0.04)))
        for sx in (-1.0, 1.0):
            ear_t = math.sin(self._phase * 2.8 + sx) * r * 0.045
            p.setBrush(self._sphere_brush(cx + sx * r * 0.62, cy - r * 0.62 + ear_t, r * 0.42, "#FFFFFF", "#E2E8F0", "#64748B"))
            p.drawEllipse(QPointF(cx + sx * r * 0.64, cy - r * 0.62 + ear_t), r * 0.30, r * 0.34)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#94A3B8"))
            p.drawEllipse(QPointF(cx + sx * r * 0.64, cy - r * 0.60 + ear_t), r * 0.16, r * 0.20)
            p.setPen(QPen(QColor("#94A3B8"), max(1.0, r * 0.04)))

        self._draw_3d_dome(p, cx, cy - r * 0.02, r * 0.94, r * 0.86, "#FFFFFF", "#E2E8F0", "#64748B")

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(51, 65, 85, 195))
        for ox, oy, rx, ry in (
            (-0.14, -0.66, 0.08, 0.07), (0.12, -0.68, 0.07, 0.06), (0.0, -0.54, 0.07, 0.06),
            (-0.34, -0.54, 0.09, 0.07), (0.34, -0.54, 0.09, 0.07),
            (-0.66, -0.06, 0.11, 0.07), (0.66, -0.06, 0.11, 0.07),
            (-0.62, 0.14, 0.10, 0.06), (0.62, 0.14, 0.10, 0.06),
        ):
            p.drawEllipse(QPointF(cx + ox * r, cy + oy * r), rx * r, ry * r)

        self._draw_eyes(p, cx, cy - r * 0.14, r * 0.32, r * 0.24, color="#FFFFFF", pupil_hex="#090D16")

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F8FAFC"))
        p.drawEllipse(QPointF(cx - r * 0.14, cy + r * 0.14), r * 0.20, r * 0.16)
        p.drawEllipse(QPointF(cx + r * 0.14, cy + r * 0.14), r * 0.20, r * 0.16)
        p.setBrush(QColor("#334155"))
        p.drawPolygon(QPolygonF([
            QPointF(cx - r * 0.12, cy + r * 0.05),
            QPointF(cx + r * 0.12, cy + r * 0.05),
            QPointF(cx, cy + r * 0.16),
        ]))
        self._draw_smile(p, cx, cy + r * 0.18, r, "#475569", 0.18)

        # Waving fuzzy front paw with dark toe beans!
        wave_a = math.sin(self._phase * 2.5)
        paw_x = cx + wave_a * r * 0.12
        paw_y = cy + r * 0.64 - abs(wave_a) * r * 0.08
        p.setBrush(self._sphere_brush(paw_x, paw_y, r * 0.56, "#FFFFFF", "#E2E8F0", "#94A3B8"))
        p.setPen(QPen(QColor("#94A3B8"), max(1.0, r * 0.045)))
        p.drawEllipse(QPointF(paw_x, paw_y), r * 0.42, r * 0.34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#18181B"))
        p.drawEllipse(QPointF(paw_x, paw_y + r * 0.09), r * 0.18, r * 0.13)
        for tx, ty in ((-0.23, -0.05), (-0.08, -0.15), (0.09, -0.15), (0.24, -0.05)):
            p.drawEllipse(QPointF(paw_x + tx * r, paw_y + ty * r), r * 0.072, r * 0.085)

    def _draw_claude(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Classic terracotta-orange pixelated Claude Code bug mascot."""
        # Subtle warm terracotta aura behind pixel silhouette
        glow = QRadialGradient(QPointF(cx, cy), r * 1.35)
        glow.setColorAt(0.0, QColor(217, 119, 87, 88))
        glow.setColorAt(1.0, QColor(217, 119, 87, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), r * 1.25, r * 1.15)

        px = max(1.6, r * 0.145)  # pixel cell unit (~9x7 sprite grid)
        hop_y = -abs(math.sin(self._phase * 2.6)) * r * 0.06
        ox = cx
        oy = cy + hop_y

        p.save()
        if self._activity_state == "sleeping":
            p.translate(ox, oy + r * 0.14)
            p.scale(1.04, 0.86)
            p.translate(-ox, -(oy + r * 0.14))

        body_col = QColor("#D97757")
        hi_col = QColor("#EB8C6E")
        sh_col = QColor("#B85839")
        rim_col = QColor("#7C2D12")

        p.setPen(Qt.PenStyle.NoPen)
        # Main rectangular pixel body (width 7px, height 4.6px)
        bx = ox - 3.5 * px
        by = oy - 2.7 * px
        bw = 7.0 * px
        bh = 4.6 * px
        p.setBrush(body_col)
        p.drawRect(QRectF(bx, by, bw, bh))
        # Subtle top bevel highlight + bottom shadow row (clean pixel depth)
        p.setBrush(hi_col)
        p.drawRect(QRectF(bx, by, bw, 0.8 * px))
        p.setBrush(sh_col)
        p.drawRect(QRectF(bx, by + bh - 0.85 * px, bw, 0.85 * px))

        # Iconic left & right pixel "arm/ear" nubs
        p.setBrush(body_col)
        p.drawRect(QRectF(ox - 4.7 * px, by + 1.15 * px, 1.25 * px, 2.1 * px))
        p.drawRect(QRectF(ox + 3.45 * px, by + 1.15 * px, 1.25 * px, 2.1 * px))

        # Two little pixel antennae / ears at top corners
        ear_wob = math.sin(self._phase * 3.0) * 0.14 * px
        p.drawRect(QRectF(ox - 2.6 * px, by - 0.9 * px + ear_wob, 1.1 * px, 0.95 * px))
        p.drawRect(QRectF(ox + 1.5 * px, by - 0.9 * px - ear_wob, 1.1 * px, 0.95 * px))

        # 4 little bottom pixel legs that alternate scuttling cleanly
        leg_l = math.sin(self._phase * 5.2) * 0.36 * px
        leg_r = -leg_l
        leg_offsets = [
            (-2.8, leg_l),
            (-1.2, leg_r),
            (0.4, leg_l),
            (2.0, leg_r),
        ]
        p.setBrush(sh_col)
        for lx, ly in leg_offsets:
            p.drawRect(QRectF(ox + lx * px, by + bh, 0.95 * px, 1.7 * px + max(0.0, ly)))

        # Pixel outline crispness
        p.setPen(QPen(rim_col, max(0.7, px * 0.14)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(bx, by, bw, bh))

        # Dark square pixel eyes following cursor gaze (blinking nicely!)
        look_px = max(-0.42, min(0.42, self._cursor_dx * 0.45)) * px
        look_py = max(-0.28, min(0.28, self._cursor_dy * 0.30)) * px
        blink = self._blink_t > 0.0 or self._activity_state == "sleeping"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1A1311"))
        eye_y = by + 1.45 * px + look_py
        if blink:
            for ex in (-1.85, 1.05):
                p.drawRect(QRectF(ox + ex * px + look_px, eye_y + 0.55 * px, 0.95 * px, 0.34 * px))
        else:
            for ex in (-1.85, 1.05):
                p.drawRect(QRectF(ox + ex * px + look_px, eye_y, 0.95 * px, 1.45 * px))
        p.restore()

    def _draw_ai_eyes(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        eye_col: str = "#FFFFFF",
        pupil_col: str = "#0F172A",
        smile: bool = True,
    ) -> None:
        lx = max(-0.22, min(0.22, self._cursor_dx * 0.22)) * r
        ly = max(-0.16, min(0.16, self._cursor_dy * 0.16)) * r
        blink = self._blink_t > 0.0 or self._activity_state == "sleeping"
        p.setPen(Qt.PenStyle.NoPen)
        for ex in (-r * 0.24, r * 0.24):
            ex_p = cx + ex + lx * 0.65
            ey_p = cy + ly * 0.65
            if blink:
                p.setBrush(QColor(eye_col))
                p.drawRoundedRect(QRectF(ex_p - r * 0.11, ey_p - r * 0.025, r * 0.22, r * 0.05), 1.2, 1.2)
            else:
                p.setBrush(QColor(eye_col))
                p.drawEllipse(QPointF(ex_p, ey_p), r * 0.125, r * 0.155)
                p.setBrush(QColor(pupil_col))
                p.drawEllipse(QPointF(ex_p + lx * 0.35, ey_p + ly * 0.35), r * 0.068, r * 0.088)
                p.setBrush(QColor(255, 255, 255, 230))
                p.drawEllipse(QPointF(ex_p - r * 0.035, ey_p - r * 0.055), r * 0.036, r * 0.036)
        if smile and not blink:
            p.setPen(QPen(QColor(eye_col), max(1.1, r * 0.065), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            sm = QPainterPath()
            sm.moveTo(cx - r * 0.10, cy + r * 0.19)
            sm.quadTo(cx, cy + r * 0.28 + self._audio_level * r * 0.08, cx + r * 0.10, cy + r * 0.19)
            p.drawPath(sm)

    def _draw_gemini(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Gemini 4-point luminous crystal sparkle mascot."""
        p.save()
        pulse = 1.0 + 0.05 * math.sin(self._phase * 1.4) + 0.12 * self._audio_level
        rr = r * 1.06 * pulse
        path = QPainterPath()
        pts = 8
        for i in range(pts):
            ang = i * (math.pi / 4.0) - math.pi / 2.0
            rad = rr if (i % 2 == 0) else (rr * 0.38)
            px = cx + math.cos(ang) * rad
            py = cy + math.sin(ang) * rad
            if i == 0:
                path.moveTo(px, py)
            else:
                prev_a = (i - 1) * (math.pi / 4.0) - math.pi / 2.0
                mid_a = (prev_a + ang) * 0.5
                ctrl_r = rr * 0.30
                path.quadTo(cx + math.cos(mid_a) * ctrl_r, cy + math.sin(mid_a) * ctrl_r, px, py)
        path.closeSubpath()
        g = QLinearGradient(QPointF(cx - rr, cy - rr), QPointF(cx + rr, cy + rr))
        g.setColorAt(0.0, QColor("#38BDF8"))
        g.setColorAt(0.45, QColor("#6366F1"))
        g.setColorAt(0.75, QColor("#A855F7"))
        g.setColorAt(1.0, QColor("#EC4899"))
        p.setPen(QPen(QColor(255, 255, 255, 165), 1.4))
        p.setBrush(g)
        p.drawPath(path)
        self._draw_ai_eyes(p, cx, cy, r * 0.78, "#FFFFFF", "#1E1B4B")
        p.restore()

    def _draw_chatgpt(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """ChatGPT emerald 3D blossom core."""
        p.save()
        g = self._sphere_brush(cx, cy, r * 1.02, "#6EE7B7", "#10A37F", "#064E3B")
        p.setPen(QPen(QColor("#A7F3D0"), 1.3))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r * 0.96, r * 0.96)
        p.setPen(QPen(QColor(255, 255, 255, 210), max(1.4, r * 0.11)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        rot = self._phase * 0.35
        for i in range(6):
            a = rot + i * (math.pi / 3.0)
            p.drawEllipse(QPointF(cx + math.cos(a) * r * 0.25, cy + math.sin(a) * r * 0.25), r * 0.36, r * 0.36)
        self._draw_ai_eyes(p, cx, cy, r * 0.88, "#FFFFFF", "#022C22")
        p.restore()

    def _draw_deepseek(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """DeepSeek detailed cerulean AI whale with dorsal fin, belly highlight, curved tail, and water jet."""
        p.save()
        rr = r * 1.06
        bob = math.sin(self._phase * 1.1) * rr * 0.04
        cy_b = cy + rr * 0.10 + bob
        # Back dorsal fin
        fin = QPainterPath()
        fin.moveTo(cx - rr * 0.12, cy_b - rr * 0.62)
        fin.cubicTo(cx + rr * 0.14, cy_b - rr * 1.02, cx + rr * 0.36, cy_b - rr * 0.72, cx + rr * 0.28, cy_b - rr * 0.56)
        fin.closeSubpath()
        p.setPen(QPen(QColor("#BAE6FD"), 1.1))
        p.setBrush(QColor("#3B82F6"))
        p.drawPath(fin)

        # Twin fluke tail
        tail = QPainterPath()
        tail.moveTo(cx + rr * 0.68, cy_b + rr * 0.04)
        tail.cubicTo(cx + rr * 1.16, cy_b - rr * 0.42, cx + rr * 1.24, cy_b - rr * 0.04, cx + rr * 0.94, cy_b + rr * 0.14)
        tail.cubicTo(cx + rr * 1.18, cy_b + rr * 0.30, cx + rr * 1.06, cy_b + rr * 0.56, cx + rr * 0.66, cy_b + rr * 0.22)
        tail.closeSubpath()
        p.setBrush(self._sphere_brush(cx + rr * 0.8, cy_b, rr * 0.5, "#93C5FD", "#4D6BFE", "#1E3A8A"))
        p.drawPath(tail)

        # Main plump whale body
        body_g = self._sphere_brush(cx - rr * 0.06, cy_b, rr * 1.04, "#93C5FD", "#4D6BFE", "#1E3A8A")
        p.setPen(QPen(QColor("#DBEAFE"), 1.3))
        p.setBrush(body_g)
        p.drawEllipse(QPointF(cx - rr * 0.05, cy_b), rr * 0.96, rr * 0.80)

        # Pale sky belly crescent
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(224, 242, 254, 155))
        p.drawEllipse(QPointF(cx - rr * 0.10, cy_b + rr * 0.44), rr * 0.64, rr * 0.26)

        # Dual water spout droplets
        sp_h = rr * (0.76 + 0.12 * abs(math.sin(self._phase * 1.3)))
        p.setBrush(QColor(224, 242, 254, 225))
        p.drawEllipse(QPointF(cx - rr * 0.20, cy_b - sp_h), rr * 0.11, rr * 0.16)
        p.drawEllipse(QPointF(cx - rr * 0.04, cy_b - sp_h - rr * 0.08), rr * 0.09, rr * 0.13)

        self._draw_ai_eyes(p, cx - rr * 0.12, cy_b - rr * 0.02, rr * 0.88, "#FFFFFF", "#0F172A")
        p.restore()

    def _draw_grok(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Grok obsidian metallic sphere with glowing white orbital slash."""
        p.save()
        g = self._sphere_brush(cx, cy, r * 1.02, "#64748B", "#0F172A", "#020617")
        p.setPen(QPen(QColor("#E2E8F0"), 1.5))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r * 0.96, r * 0.96)
        p.setPen(QPen(QColor(255, 255, 255, 220), max(2.0, r * 0.16), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - r * 0.62, cy + r * 0.48), QPointF(cx + r * 0.62, cy - r * 0.48))
        self._draw_ai_eyes(p, cx, cy + r * 0.04, r * 0.88, "#F8FAFC", "#020617")
        p.restore()

    def _draw_perplexity(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Perplexity teal faceted prism with crisp crosshair."""
        p.save()
        g = self._sphere_brush(cx, cy, r * 1.02, "#99F6E4", "#1FB8CD", "#134E4A")
        p.setPen(QPen(QColor("#CCFBF1"), 1.3))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(cx - r * 0.84, cy - r * 0.84, r * 1.68, r * 1.68), r * 0.36, r * 0.36)
        p.setPen(QPen(QColor(255, 255, 255, 195), 1.4))
        p.drawLine(QPointF(cx, cy - r * 0.70), QPointF(cx, cy + r * 0.70))
        p.drawLine(QPointF(cx - r * 0.64, cy), QPointF(cx + r * 0.64, cy))
        self._draw_ai_eyes(p, cx, cy, r * 0.88, "#FFFFFF", "#042F2E")
        p.restore()

    def _draw_qwen(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Qwen purple crystal hexagon with inner geometric star."""
        p.save()
        poly = QPolygonF([
            QPointF(cx + r * 0.98 * math.cos(i * math.pi / 3.0 - math.pi / 6.0),
                    cy + r * 0.98 * math.sin(i * math.pi / 3.0 - math.pi / 6.0))
            for i in range(6)
        ])
        g = self._sphere_brush(cx, cy, r * 1.02, "#DDD6FE", "#7C3AED", "#3B0764")
        p.setPen(QPen(QColor("#EDE9FE"), 1.4))
        p.setBrush(g)
        p.drawPolygon(poly)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        inner = QPolygonF([
            QPointF(cx + r * 0.64 * math.cos(i * math.pi / 3.0),
                    cy + r * 0.64 * math.sin(i * math.pi / 3.0))
            for i in range(6)
        ])
        p.drawPolygon(inner)
        self._draw_ai_eyes(p, cx, cy, r * 0.88, "#FFFFFF", "#2E1065")
        p.restore()

    def _draw_llama(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Meta Llama cyan sphere with infinity ribbon."""
        p.save()
        g = self._sphere_brush(cx, cy, r * 1.02, "#BAE6FD", "#0284C7", "#0C4A6E")
        p.setPen(QPen(QColor("#E0F2FE"), 1.3))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r * 0.95, r * 0.95)
        p.setPen(QPen(QColor(255, 255, 255, 230), max(2.0, r * 0.15)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx - r * 0.28, cy), r * 0.30, r * 0.26)
        p.drawEllipse(QPointF(cx + r * 0.28, cy), r * 0.30, r * 0.26)
        self._draw_ai_eyes(p, cx, cy, r * 0.88, "#FFFFFF", "#082F49")
        p.restore()

    def _draw_mistral(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Mistral layered warm flame pixel crest."""
        p.save()
        p.setPen(QPen(QColor(255, 255, 255, 90), 0.9))
        colors = ("#FACC15", "#FB923C", "#F97316", "#EA580C")
        widths = (1.30, 1.56, 1.56, 1.36)
        for idx, (col, wf) in enumerate(zip(colors, widths)):
            p.setBrush(QColor(col))
            y_row = cy - r * 0.62 + idx * r * 0.32
            w_row = r * wf
            p.drawRoundedRect(QRectF(cx - w_row * 0.5, y_row, w_row, r * 0.28), r * 0.10, r * 0.10)
        self._draw_ai_eyes(p, cx, cy + r * 0.04, r * 0.90, "#FFFFFF", "#1F2937")
        p.restore()

    def _draw_copilot(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        """Copilot vibrant tricolor ribbon capsule."""
        p.save()
        g = QLinearGradient(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
        g.setColorAt(0.0, QColor("#38BDF8"))
        g.setColorAt(0.35, QColor("#A855F7"))
        g.setColorAt(0.7, QColor("#F43F5E"))
        g.setColorAt(1.0, QColor("#F59E0B"))
        p.setPen(QPen(QColor(255, 255, 255, 175), 1.4))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(cx - r * 0.92, cy - r * 0.80, r * 1.84, r * 1.60), r * 0.52, r * 0.52)
        self._draw_ai_eyes(p, cx, cy, r * 0.88, "#FFFFFF", "#1E1B4B")
        p.restore()

    # ------------------------------------------------------------------ 20 New Live Characters
    def _draw_kuro_neko(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        self._draw_eared_mammal(
            p, cx, cy, r, "#475569", "#090D16", "#F43F5E", cheek="#F43F5E", whiskers=True, ear_twitch=True
        )
        # Golden glowing sclera override accents
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.34, r * 0.21, color="#FDE047", pupil_hex="#090D16")

    def _draw_sakura_kitsune(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        self._draw_eared_mammal(
            p, cx, cy, r, "#FBCFE8", "#BE185D", "#FFF1F2", cheek="#FB7185", muzzle_patch=True, tail_colors=("#F472B6", "#FFF1F2")
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F43F5E"))
        p.drawEllipse(QPointF(cx, cy - r * 0.48), r * 0.11, r * 0.15)

    def _draw_crystal_fox(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        self._draw_eared_mammal(
            p, cx, cy, r, "#BAE6FD", "#0284C7", "#E0F2FE", cheek="#38BDF8", brows=True, muzzle_patch=True, tail_colors=("#38BDF8", "#F0F9FF")
        )

    def _draw_cyber_skull(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        g = self._sphere_brush(cx, cy, r, "#F8FAFC", "#94A3B8", "#334155")
        p.setPen(QPen(QColor("#38BDF8"), 1.3))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy - r * 0.08), r * 0.88, r * 0.82)
        p.drawRoundedRect(QRectF(cx - r * 0.46, cy + r * 0.36, r * 0.92, r * 0.48), r * 0.16, r * 0.16)
        # Glowing neon visor sockets
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.32, r * 0.23, color="#22D3EE", pupil_hex="#082F49")
        p.restore()

    def _draw_void_eye(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        ang = self._phase * 1.4
        p.setPen(QPen(_c(self._accent, 175), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(6):
            a = ang + i * math.pi / 3.0
            p.drawEllipse(QPointF(cx + math.cos(a) * r * 1.05, cy + math.sin(a) * r * 1.05), r * 0.10, r * 0.10)
        self._draw_3d_dome(p, cx, cy, r * 0.92, r * 0.92, "#FFFFFF", "#CBD5E1", "#334155", rim_alpha=180)
        lx = self._cursor_dx * r * 0.32
        ly = self._cursor_dy * r * 0.32
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QRadialGradient(QPointF(cx + lx, cy + ly), r * 0.48))
        ig = QRadialGradient(QPointF(cx + lx, cy + ly), r * 0.48)
        ig.setColorAt(0.0, QColor("#090D16"))
        ig.setColorAt(0.45, self._accent)
        ig.setColorAt(1.0, QColor("#312E81"))
        p.setBrush(ig)
        p.drawEllipse(QPointF(cx + lx, cy + ly), r * 0.46, r * 0.46)
        p.setBrush(QColor("#020617"))
        p.drawEllipse(QPointF(cx + lx, cy + ly), r * 0.22, r * 0.22)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(cx + lx - r * 0.16, cy + ly - r * 0.16), r * 0.11, r * 0.11)
        p.restore()

    def _draw_mech_unit(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        self._draw_3d_dome(p, cx, cy, r * 0.92, r * 0.88, "#C084FC", "#6D28D9", "#2E1065")
        p.setPen(QPen(QColor("#A3E635"), 1.6))
        p.setBrush(QColor("#111827"))
        p.drawRoundedRect(QRectF(cx - r * 0.68, cy - r * 0.24, r * 1.36, r * 0.56), r * 0.18, r * 0.18)
        self._draw_eyes(p, cx, cy + r * 0.02, r * 0.32, r * 0.19, color="#A3E635", pupil_hex="#052E16")
        p.restore()

    def _draw_ninja(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        # Flowing headband ribbon
        wag = math.sin(self._phase * 2.4) * r * 0.16
        p.setPen(QPen(QColor("#EF4444"), max(2.2, r * 0.20), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx + r * 0.72, cy - r * 0.24), QPointF(cx + r * 1.24, cy - r * 0.12 + wag))
        self._draw_3d_dome(p, cx, cy, r * 0.92, r * 0.90, "#475569", "#1E293B", "#020617")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EF4444"))
        p.drawRoundedRect(QRectF(cx - r * 0.90, cy - r * 0.42, r * 1.80, r * 0.22), r * 0.08, r * 0.08)
        p.setBrush(QColor("#FDE68A"))
        p.drawRoundedRect(QRectF(cx - r * 0.68, cy - r * 0.14, r * 1.36, r * 0.36), r * 0.14, r * 0.14)
        self._draw_eyes(p, cx, cy + r * 0.04, r * 0.30, r * 0.16, color="#0F172A", pupil_hex="#38BDF8")
        p.restore()

    def _draw_samurai(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        # Kabuto crescent horns
        p.setPen(QPen(QColor("#FACC15"), max(2.0, r * 0.18), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy - r * 0.56), QPointF(cx - r * 0.72, cy - r * 1.04))
        p.drawLine(QPointF(cx, cy - r * 0.56), QPointF(cx + r * 0.72, cy - r * 1.04))
        self._draw_3d_dome(p, cx, cy + r * 0.06, r * 0.92, r * 0.86, "#F87171", "#991B1B", "#450A0A")
        p.setBrush(QColor("#111827"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(cx - r * 0.64, cy - r * 0.16, r * 1.28, r * 0.46), r * 0.14, r * 0.14)
        self._draw_eyes(p, cx, cy + r * 0.06, r * 0.29, r * 0.17, color="#FDE047", pupil_hex="#713F12")
        p.restore()

    def _draw_coffee_cup(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        p.setPen(QPen(QColor("#E2E8F0"), max(1.8, r * 0.14)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx + r * 0.48, cy - r * 0.18, r * 0.54, r * 0.56), -90 * 16, 180 * 16)
        self._draw_3d_dome(p, cx - r * 0.06, cy + r * 0.10, r * 0.78, r * 0.78, "#FFF7ED", "#D97706", "#78350F")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FDE68A"))
        p.drawEllipse(QPointF(cx - r * 0.06, cy - r * 0.42), r * 0.60, r * 0.18)
        self._draw_eyes(p, cx - r * 0.06, cy + r * 0.14, r * 0.26, r * 0.16, color="#FFFFFF", pupil_hex="#451A03")
        p.restore()

    def _draw_sushi_roll(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        self._draw_3d_dome(p, cx, cy, r * 0.90, r * 0.78, "#FFFFFF", "#F8FAFC", "#CBD5E1")
        # Nori belt + orange salmon crest
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0F172A"))
        p.drawRoundedRect(QRectF(cx - r * 0.86, cy + r * 0.16, r * 1.72, r * 0.32), r * 0.10, r * 0.10)
        p.setBrush(QColor("#FB923C"))
        p.drawRoundedRect(QRectF(cx - r * 0.76, cy - r * 0.74, r * 1.52, r * 0.34), r * 0.16, r * 0.16)
        self._draw_eyes(p, cx, cy - r * 0.08, r * 0.28, r * 0.16, color="#0F172A")
        p.restore()

    def _draw_cassette(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        g = QLinearGradient(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
        g.setColorAt(0.0, QColor("#334155"))
        g.setColorAt(1.0, QColor("#0F172A"))
        p.setPen(QPen(self._accent, 1.3))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(cx - r * 0.96, cy - r * 0.66, r * 1.92, r * 1.32), r * 0.20, r * 0.20)
        p.setBrush(QColor("#F59E0B"))
        p.drawRoundedRect(QRectF(cx - r * 0.76, cy - r * 0.44, r * 1.52, r * 0.56), r * 0.10, r * 0.10)
        self._draw_eyes(p, cx, cy - r * 0.16, r * 0.36, r * 0.18, color="#0F172A", pupil_hex="#FFFFFF")
        p.restore()

    def _draw_vinyl_disc(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        self._draw_3d_dome(p, cx, cy, r * 0.96, r * 0.96, "#334155", "#0F172A", "#020617")
        p.setPen(QPen(QColor(255, 255, 255, 55), 0.9))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r * 0.72, r * 0.72)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawEllipse(QPointF(cx, cy), r * 0.44, r * 0.44)
        self._draw_eyes(p, cx, cy, r * 0.21, r * 0.13, color="#FFFFFF", pupil_hex="#020617")
        p.restore()

    def _draw_pixel_heart(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        pulse = 1.0 + 0.08 * math.sin(self._phase * 3.2) + self._audio_level * 0.14
        hr = r * pulse
        path = QPainterPath()
        path.moveTo(cx, cy + hr * 0.80)
        path.cubicTo(cx - hr * 1.15, cy + hr * 0.15, cx - hr * 0.95, cy - hr * 0.85, cx, cy - hr * 0.30)
        path.cubicTo(cx + hr * 0.95, cy - hr * 0.85, cx + hr * 1.15, cy + hr * 0.15, cx, cy + hr * 0.80)
        p.setPen(QPen(QColor("#FDA4AF"), 1.3))
        p.setBrush(self._sphere_brush(cx, cy, hr, "#FB7185", "#E11D48", "#881337"))
        p.drawPath(path)
        self._draw_eyes(p, cx, cy - r * 0.02, r * 0.28, r * 0.16, color="#FFFFFF", pupil_hex="#4C0519")
        p.restore()

    def _draw_planet_saturn(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        self._draw_3d_dome(p, cx, cy, r * 0.74, r * 0.74, "#FDE68A", "#F59E0B", "#92400E")
        p.save()
        p.translate(cx, cy)
        p.rotate(-22.0 + math.sin(self._phase * 0.7) * 5.0)
        p.setPen(QPen(QColor(224, 242, 254, 210), max(2.2, r * 0.16)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0.0, 0.0), r * 1.20, r * 0.34)
        p.restore()
        self._draw_eyes(p, cx, cy, r * 0.25, r * 0.15, color="#FFFFFF", pupil_hex="#451A03")
        p.restore()

    def _draw_lightning_spark(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        bolt = QPolygonF([
            QPointF(cx + r * 0.18, cy - r * 0.98),
            QPointF(cx - r * 0.56, cy + r * 0.08),
            QPointF(cx - r * 0.04, cy + r * 0.08),
            QPointF(cx - r * 0.22, cy + r * 0.98),
            QPointF(cx + r * 0.58, cy - r * 0.10),
            QPointF(cx + r * 0.08, cy - r * 0.10),
        ])
        p.setPen(QPen(QColor("#FEF08A"), 1.4))
        p.setBrush(self._sphere_brush(cx, cy, r, "#FEF08A", "#FACC15", "#EA580C"))
        p.drawPolygon(bolt)
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.23, r * 0.14, color="#0F172A", pupil_hex="#FFFFFF")
        p.restore()

    def _draw_pumpkin_jack(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        p.setPen(QPen(QColor("#15803D"), max(2.2, r * 0.20), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy - r * 0.70), QPointF(cx + r * 0.12, cy - r * 0.98))
        self._draw_3d_dome(p, cx, cy + r * 0.06, r * 0.96, r * 0.80, "#FDBA74", "#F97316", "#9A3412")
        self._draw_eyes(p, cx, cy + r * 0.02, r * 0.30, r * 0.18, color="#FEF08A", pupil_hex="#7C2D12")
        p.restore()

    def _draw_tamagotchi(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        self._draw_3d_dome(p, cx, cy, r * 0.82, r * 0.98, "#F9A8D4", "#EC4899", "#831843")
        p.setPen(QPen(QColor("#FCE7F3"), 1.1))
        p.setBrush(QColor("#A7F3D0"))
        p.drawRoundedRect(QRectF(cx - r * 0.50, cy - r * 0.38, r * 1.00, r * 0.68), r * 0.14, r * 0.14)
        self._draw_eyes(p, cx, cy - r * 0.04, r * 0.22, r * 0.14, color="#064E3B")
        p.restore()

    def _draw_astro_helmet(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        self._draw_3d_dome(p, cx, cy, r * 0.94, r * 0.90, "#F8FAFC", "#E2E8F0", "#94A3B8")
        p.setPen(QPen(QColor("#38BDF8"), 1.2))
        p.setBrush(self._sphere_brush(cx, cy, r * 0.72, "#38BDF8", "#0F172A", "#020617"))
        p.drawRoundedRect(QRectF(cx - r * 0.62, cy - r * 0.42, r * 1.24, r * 0.84), r * 0.36, r * 0.36)
        self._draw_eyes(p, cx, cy, r * 0.28, r * 0.17, color="#7DD3FC", pupil_hex="#082F49")
        p.restore()

    # ------------------------------------------------------------------ Overlays
    def _draw_alarm_halo(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        pulse = (math.sin(self._phase * 7.5) + 1.0) * 0.5
        col = QColor("#EF4444" if pulse > 0.5 else "#F59E0B")
        col.setAlpha(int(150 + 90 * pulse))
        p.setPen(QPen(col, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_thinking_ring(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.setPen(QPen(QColor(255, 255, 255, 190), 2.0))
        a = self._phase * 2.6
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(math.degrees(a) * 16), 95 * 16)
        # Bouncing animated "..." thinking dots above character
        p.setPen(Qt.PenStyle.NoPen)
        for i, dx in enumerate((-5.0, 0.0, 5.0)):
            hop = max(0.0, math.sin(self._phase * 3.4 - i * 0.9))
            p.setBrush(QColor(255, 255, 255, int(145 + 100 * hop)))
            p.drawEllipse(QPointF(cx + dx, cy - r * 0.98 - hop * 3.2), 1.6, 1.6)

    def _draw_precip_drop(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        drop_t = (self._phase * 0.45) % 1.0
        dy = cy + r * 0.6 + drop_t * r * 0.7
        col = QColor("#E0F2FE" if self._precip == "snow" else "#60A5FA")
        col.setAlpha(int(210 * (1.0 - drop_t)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(cx + r * 0.78, dy), 2.2, 2.2)

    def _draw_sleep_zzz(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        t = self._sleep_phase
        col = QColor("#93C5FD")
        col.setAlpha(int(235 * (1.0 - t)))
        p.setPen(QPen(col, 1.4))
        zx = cx + r * (0.45 + 0.5 * t)
        zy = cy - r * (0.55 + 0.9 * t)
        s = 3.5 + 2.5 * t
        p.drawLine(QPointF(zx, zy), QPointF(zx + s, zy))
        p.drawLine(QPointF(zx + s, zy), QPointF(zx, zy + s))
        p.drawLine(QPointF(zx, zy + s), QPointF(zx + s, zy + s))

    def _draw_music_note(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        t = (self._phase * 0.22) % 1.0
        col = QColor(self._accent).lighter(130)
        col.setAlpha(int(220 * (1.0 - t)))
        p.setPen(QPen(col, 1.5))
        p.setBrush(col)
        nx = cx - r * (0.48 + 0.45 * t)
        ny = cy - r * (0.5 + 0.85 * t)
        p.drawEllipse(QPointF(nx, ny), 2.3, 1.8)
        p.drawLine(QPointF(nx + 2.0, ny), QPointF(nx + 2.0, ny - 6.5))

    def _draw_idle_spark(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        t = (math.sin(self._phase * 0.55) + 1.0) * 0.5
        if t < 0.72:
            return
        alpha = int(170 * (t - 0.72) / 0.28)
        col = QColor(255, 255, 255, alpha)
        p.setPen(QPen(col, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        sx = cx + r * 0.86
        sy = cy - r * 0.84
        p.drawLine(QPointF(sx - 2.6, sy), QPointF(sx + 2.6, sy))
        p.drawLine(QPointF(sx, sy - 2.6), QPointF(sx, sy + 2.6))

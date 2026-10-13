#a small slider widget used for volumes and mic sensitivity
from __future__ import annotations

from typing import Callable, Optional

from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget


class Slider(Widget, can_focus=True):

    DEFAULT_CSS = """
    Slider { height: 1; width: 1fr; }
    Slider:focus { text-style: bold; }
    """

    BINDINGS = [
        ("left,h", "nudge(-1)", "Down"),
        ("right,l", "nudge(1)", "Up"),
        ("shift+left,pagedown", "nudge(-10)", "Down more"),
        ("shift+right,pageup", "nudge(10)", "Up more"),
        ("home", "edge(0)", "Min"),
        ("end", "edge(1)", "Max"),
    ]

    value = reactive(0.0)

    class Changed(Message):
        def __init__(self, slider: "Slider", value: float) -> None:
            super().__init__()
            self.slider = slider
            self.value = value

        @property
        def control(self) -> "Slider":
            return self.slider

    def __init__(
        self,
        value: float,
        lo: float,
        hi: float,
        step: float = 1,
        *,
        fmt: Callable[[float], str] = lambda v: f"{v:.0f}%",
        default: Optional[float] = None,
        marker: Optional[float] = None,
        **kw,
    ) -> None:
        super().__init__(**kw)
        self.lo, self.hi, self.step = lo, hi, step
        self.fmt = fmt
        self.default = default
        self.marker = marker
        self._dragging = False
        self.set_reactive(Slider.value, self._clamp(value))

    def _clamp(self, v: float) -> float:
        v = max(self.lo, min(self.hi, v))
        return round(v / self.step) * self.step

    def watch_value(self, old: float, new: float) -> None:
        if old != new:
            self.post_message(self.Changed(self, new))

    def _barWidth(self) -> int:
        return max(4, self.size.width - 8)

    def _frac(self, v: float) -> float:
        return (v - self.lo) / (self.hi - self.lo) if self.hi != self.lo else 0

    def render(self) -> Text:
        w = self._barWidth()
        knob = min(w - 1, int(round(self._frac(self.value) * (w - 1))))
        mark = (
            None if self.marker is None else min(w - 1, max(0, int(round(self._frac(self.marker) * (w - 1)))))
        )
        tick = None if self.default is None else min(w - 1, int(round(self._frac(self.default) * (w - 1))))
        t = Text()
        for i in range(w):
            if i == knob:
                t.append("●", style="bold white" if self.has_focus else "bold")
            elif mark is not None and i <= mark:
                t.append("█", style="green")
            elif i < knob:
                t.append("━", style="cyan")
            elif i == tick:
                t.append("┃", style="bright_black")
            else:
                t.append("─", style="bright_black")
        t.append(f" {self.fmt(self.value):>6}", style="bold" if self.has_focus else "")
        return t

    def action_nudge(self, n: int) -> None:
        self.value = self._clamp(self.value + n * self.step)

    def action_edge(self, top: int) -> None:
        self.value = self.hi if top else self.lo

    def _setFromX(self, x: int) -> None:
        w = self._barWidth()
        if 0 <= x < w + 1:
            v = self._clamp(self.lo + (min(x, w - 1) / (w - 1)) * (self.hi - self.lo))
            if self.default is not None and abs(v - self.default) <= (self.hi - self.lo) * 0.03:
                v = self.default
            self.value = v

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self.focus()
        self._dragging = True
        self.capture_mouse()
        self._setFromX(event.x)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            self._setFromX(event.x)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._dragging = False
        self.release_mouse()

    def on_key(self, event: events.Key) -> None:
        if event.character and event.character.isdigit():
            self.value = self._clamp(self.lo + int(event.character) / 10 * (self.hi - self.lo))
            event.stop()

    def setMarker(self, v: Optional[float]) -> None:
        self.marker = v
        self.refresh()

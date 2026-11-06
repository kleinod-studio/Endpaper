"""How far a book has been read, as a ring: the same ring that shows the writing goal in Typecase."""

import math

from gi.repository import Adw, Gdk, Graphene, Gtk

FINISHED = 0.99  # the last page seldom reaches 100 %

_css_installed = False


def install_css() -> None:
    """The disc that keeps the ring readable on any cover, and the card in the corner of the open book,
    styled like the writing goal card in Typecase."""
    global _css_installed
    if _css_installed:
        return
    _css_installed = True
    provider = Gtk.CssProvider()
    provider.load_from_string("""
        /* Dark like the buttons on the cover, but without their white, which would take the ring's color away. */
        .progress-disc { background-color: alpha(black, 0.65); border-radius: 999px; padding: 4px; }
        .progress-badge {
            background-color: var(--popover-bg-color);
            color: var(--popover-fg-color);
            border-radius: 14px;
            box-shadow: 0 0 0 1px alpha(currentColor, 0.1), 0 3px 10px alpha(black, 0.2);
            padding: 8px 12px;
        }
        .progress-badge:hover { background-image: image(alpha(currentColor, 0.05)); }
        .progress-badge:active { background-image: image(alpha(currentColor, 0.1)); }
    """)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


class ProgressRing(Gtk.Widget):
    """Its color comes from the style (CSS classes accent and success), so it follows the accent color as well
    as light and dark mode. A finished book turns it green, like a writing goal reached."""

    __gtype_name__ = "EndpaperProgressRing"

    def __init__(self, size: int, thickness: float, **kwargs):
        # A progress bar for screen readers, which announce the percentage.
        super().__init__(css_classes=["accent"], accessible_role=Gtk.AccessibleRole.PROGRESS_BAR, **kwargs)
        self.update_property([Gtk.AccessibleProperty.VALUE_MIN, Gtk.AccessibleProperty.VALUE_MAX], [0.0, 100.0])
        self.size = size
        self.thickness = thickness
        self.value = 0.0
        self._animation = Adw.TimedAnimation(
            widget=self, value_from=0, value_to=0, duration=450, easing=Adw.Easing.EASE_OUT_CUBIC,
            target=Adw.CallbackAnimationTarget.new(self._set_value),
        )

    def show_progress(self, value: float, animate: bool = True) -> None:
        """Glides to the new value (libadwaita skips this when animations are turned off)."""
        value = max(0.0, min(float(value), 1.0))  # a finished book can come as the whole number 1
        self.set_css_classes(["success" if value >= FINISHED else "accent"])
        self.update_property([Gtk.AccessibleProperty.VALUE_NOW], [round(value * 100, 1)])
        self._animation.pause()
        if animate and self.get_mapped():
            self._animation.set_value_from(self.value)
            self._animation.set_value_to(value)
            self._animation.play()
        else:
            self._set_value(value)

    def _set_value(self, value: float):
        self.value = value
        self.queue_draw()

    def do_measure(self, _orientation, _for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
        color = self.get_color()
        radius = min(width, height) / 2 - self.thickness / 2
        cx, cy = width / 2, height / 2
        cr.set_line_width(self.thickness)
        cr.set_source_rgba(color.red, color.green, color.blue, 0.25)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if self.value > 0:
            cr.set_line_cap(1)  # round
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            start = -math.pi / 2  # from the top, clockwise
            # A finished book closes the ring; otherwise even the last page leaves a gap that shows it is not.
            end = 2 * math.pi if self.value >= FINISHED else 2 * math.pi * self.value
            cr.arc(cx, cy, radius, start, start + end)
            cr.stroke()

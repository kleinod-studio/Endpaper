"""A text field for notes that shows Markdown as it is typed, as Typecase does."""

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .markdown_style import FENCE, line_spans


class MarkdownView(Gtk.TextView):
    def __init__(self, text: str = ""):
        # accepts_tab: the Tab key goes on to the next control, so that everything stays reachable by keyboard.
        super().__init__(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=4, bottom_margin=4, accepts_tab=False, css_classes=["note-view"])
        self.tags = {}
        self._last_width = 0
        buffer = self.get_buffer()

        def tag(name, **properties):
            self.tags[name] = buffer.create_tag(f"md-{name}", **properties)

        # Order = priority: later tags win, so that the dimmed marks stay dimmed.
        tag("h1", scale=1.4, weight=Pango.Weight.BOLD)
        tag("h2", scale=1.25, weight=Pango.Weight.BOLD)
        tag("h3", scale=1.1, weight=Pango.Weight.BOLD)
        for level in (4, 5, 6):
            tag(f"h{level}", weight=Pango.Weight.BOLD)
        tag("bold", weight=Pango.Weight.BOLD)
        tag("italic", style=Pango.Style.ITALIC)
        tag("strike", strikethrough=True)
        tag("underline", underline=Pango.Underline.SINGLE)
        tag("quote", style=Pango.Style.ITALIC)
        tag("code", family="monospace")
        tag("codeblock", family="monospace")
        tag("link", underline=Pango.Underline.SINGLE)
        tag("list-marker", weight=Pango.Weight.BOLD)
        tag("syntax")
        manager = Adw.StyleManager.get_default()
        self._style_handler = manager.connect("notify::dark", lambda *_a: self._update_colors())
        self.connect("destroy", lambda _v: manager.disconnect(self._style_handler))
        self._update_colors()
        buffer.set_text(text)
        buffer.connect("changed", lambda _b: self.restyle())
        self.restyle()

    def do_size_allocate(self, width, height, baseline):
        Gtk.TextView.do_size_allocate(self, width, height, baseline)
        # How high wrapped text is only shows once the width is known: otherwise lines get cut off.
        if width != self._last_width:
            self._last_width = width
            GLib.idle_add(self._measure_again)

    def _measure_again(self):
        self.queue_resize()
        return GLib.SOURCE_REMOVE

    def _update_colors(self):
        color = Gdk.RGBA()
        color.parse("rgba(255, 255, 255, .45)" if Adw.StyleManager.get_default().get_dark() else "rgba(0, 0, 0, .45)")
        self.tags["syntax"].props.foreground_rgba = color

    @property
    def text(self) -> str:
        buffer = self.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def restyle(self):
        """Notes are short enough to go through as a whole on every change."""
        buffer = self.get_buffer()
        start, end = buffer.get_bounds()
        for tag in self.tags.values():
            buffer.remove_tag(tag, start, end)
        in_code_block = False
        for number, line in enumerate(self.text.split("\n")):
            fence = FENCE.match(line)
            for first, last, name in line_spans(line, in_code_block):
                tag = self.tags.get(name)
                if tag:
                    _found, from_iter = buffer.get_iter_at_line_offset(number, first)
                    _found, to_iter = buffer.get_iter_at_line_offset(number, last)
                    buffer.apply_tag(tag, from_iter, to_iter)
            if fence:
                in_code_block = not in_code_block

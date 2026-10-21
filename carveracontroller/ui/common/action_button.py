"""Shared icon button used by popups and dialogs."""

from kivy.core.window import Window
from kivy.factory import Factory
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, StringProperty

from carveracontroller.addons.tooltips.Tooltips import ToolTipButton


class PopupActionButton(ToolTipButton):
    """Standard app Button with an optional leading icon and tinted variants."""

    icon = StringProperty("")
    btn_text = StringProperty("")
    primary = BooleanProperty(False)
    destructive = BooleanProperty(False)
    flat = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # ToolTipButton KV draws a rounded overlay; keep the atlas/tint look.
        self.canvas.before.clear()
        self.bind(
            state=self._sync_colors,
            primary=self._sync_colors,
            destructive=self._sync_colors,
            disabled=self._sync_colors,
            flat=self._sync_colors,
            pos=self._redraw_flat,
            size=self._redraw_flat,
            texture=self._redraw_flat,
            texture_size=self._redraw_flat,
            color=self._redraw_flat,
            background_color=self._redraw_flat,
            icon=self._redraw_flat,
        )
        self._sync_colors()

    def _sync_colors(self, *_args):
        down = self.state == "down"
        if self.disabled:
            self.color = [160 / 255, 160 / 255, 160 / 255, 1]
            if self.flat:
                self.background_normal = ""
                self.background_down = ""
                self.background_color = [50 / 255, 50 / 255, 50 / 255, 1]
            else:
                self.background_normal = "atlas://data/images/defaulttheme/button_disabled"
                self.background_down = "atlas://data/images/defaulttheme/button_disabled"
                self.background_color = [1, 1, 1, 1]
            self._redraw_flat()
            return
        self.color = [1, 1, 1, 1]
        if self.flat:
            # Solid fill so primary/destructive colors stay true; used on the footer.
            self.background_normal = ""
            self.background_down = ""
            if self.destructive:
                self.background_color = (
                    [150 / 255, 55 / 255, 55 / 255, 1] if down else [186 / 255, 72 / 255, 72 / 255, 1]
                )
            elif self.primary:
                self.background_color = (
                    [32 / 255, 114 / 255, 148 / 255, 1] if down else [50 / 255, 164 / 255, 206 / 255, 1]
                )
            else:
                self.background_color = [64 / 255, 64 / 255, 64 / 255, 1] if down else [88 / 255, 88 / 255, 88 / 255, 1]
            self._redraw_flat()
            return
        self.background_normal = "atlas://data/images/defaulttheme/button"
        self.background_down = "atlas://data/images/defaulttheme/button_pressed"
        self.background_color = [1, 1, 1, 1]

    def _redraw_flat(self, *_args):
        """Left-align label on flat buttons so gaps match the 10dp icon inset."""
        if not self.flat:
            return
        inset = dp(10)
        icon_size = dp(20) if self.icon else 0
        text_x = self.x + inset + (icon_size + inset if self.icon else 0)
        text_h = self.texture_size[1] if self.texture_size else 0
        self.canvas.clear()
        with self.canvas:
            Color(rgba=self.background_color)
            Rectangle(pos=self.pos, size=self.size)
            if self.texture:
                Color(rgba=self.color)
                Rectangle(
                    texture=self.texture,
                    size=self.texture_size,
                    pos=(text_x, self.center_y - text_h / 2.0),
                )

    def on_parent(self, _instance, parent):
        if parent is None:
            self.close_tooltip()
            Window.unbind(mouse_pos=self.on_mouse_pos)


if "PopupActionButton" not in Factory.classes:
    Factory.register("PopupActionButton", cls=PopupActionButton)

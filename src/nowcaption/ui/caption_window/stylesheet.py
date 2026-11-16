"""The caption box's stylesheet: text size, colour, font and background, the status row, and the
menu's colour dots and position screens. It also styles the sample caption under More options
(.caption-preview). Text only: the window loads it into its CSS provider."""
from __future__ import annotations

from ...style import CHANGE_MS, COLOURS, PAD_X, PAD_Y, css_font
from ..preview import Preview


def caption_css(px: int, font: str, alpha: float, colour: str) -> str:
    """The stylesheet for text px pixels high in font (empty: the desktop's), in colour (such as
    '#ffffff'), on a background of opacity alpha (0 to 1)."""
    family = f'font-family: {css_font(font)};' if font.strip() else ''
    swatches = ''.join(f'window.nowcaption .colour-{name} {{ background: {rgb}; }}\n'
                       for name, (_, rgb) in COLOURS.items())
    return f"""
            window.nowcaption {{ background: transparent; }}
            window.nowcaption .caption-box {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px;
                                             transition: background-color {CHANGE_MS}ms ease-out; }}
            window.nowcaption .caption-text {{ color: {colour}; font-size: {px}px; font-weight: 500; {family}
                                              transition: color {CHANGE_MS}ms ease-out; }}
            .caption-preview {{ background: rgba(0, 0, 0, {alpha}); border-radius: 14px; }}
            .caption-preview .caption-text {{ color: {colour}; font-size: {min(px, Preview.MAX_SIZE)}px;
                                             font-weight: 500; {family} transition: color {CHANGE_MS}ms ease-out; }}
            window.nowcaption .caption-status {{ color: #cccccc; font-size: {max(14, round(px * 0.55))}px;
                                                padding: 0 {PAD_X}px {PAD_Y}px; }}
            window.nowcaption.docked .caption-box {{ border-radius: 0; }}
            window.nowcaption .caption-status.corner {{ background: rgba(0, 0, 0, 0.85); border-radius: 8px;
                                                       padding: 2px 10px; margin: 6px; }}
            window.nowcaption .screen {{ min-width: 22px; min-height: 15px; border-radius: 3px;
                                        border: 1.5px solid alpha(currentColor, 0.7); }}
            window.nowcaption .screen .strip {{ background: currentColor; border-radius: 1px; }}
            window.nowcaption .screen .strip.across {{ min-height: 4px; }}
            window.nowcaption .screen .strip.down {{ min-width: 5px; }}
            window.nowcaption .screen .strip.floating {{ min-width: 11px; min-height: 4px; margin-bottom: 2px; }}
            window.nowcaption button.position {{ padding: 5px 7px; min-height: 0; min-width: 0; }}
            window.nowcaption button.position:checked {{ background: alpha(@accent_bg_color, 0.25); }}
            window.nowcaption button.position:checked .screen {{ color: @accent_color; }}
            window.nowcaption .colour-swatch {{ min-width: 18px; min-height: 18px; border-radius: 99px;
                                               border: 1px solid rgba(0, 0, 0, 0.4); }}
            window.nowcaption button.colour-dot {{ padding: 3px; min-width: 0; min-height: 0;
                                                  border-radius: 99px; }}
            window.nowcaption button.colour-dot:checked {{ background: none; }}
            window.nowcaption button.colour-dot:checked .colour-swatch {{
                box-shadow: 0 0 0 2px @popover_bg_color, 0 0 0 3px currentColor; }}
            {swatches}"""

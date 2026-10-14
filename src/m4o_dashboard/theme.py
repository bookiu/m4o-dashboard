"""Shared colors for TCSS and Rich-rendered dashboard content."""

from textual.theme import Theme

CYAN = "#31d2ed"
PURPLE = "#bd88f5"
GREEN = "#43e89a"
MUTED = "#7892ac"
FOREGROUND = "#bdd2e8"
BACKGROUND = "#0b131c"

DASHBOARD_THEME = Theme(
    name="m4o",
    primary=CYAN,
    secondary=PURPLE,
    accent=CYAN,
    success=GREEN,
    warning="#f4cf60",
    error="#ff777e",
    foreground=FOREGROUND,
    background=BACKGROUND,
    surface="#101d2a",
    panel=BACKGROUND,
    dark=True,
    variables={
        "panel-border": "#537b9f",
        "panel-divider": "#2a465e",
        "block-cursor-background": CYAN,
        "block-cursor-foreground": BACKGROUND,
        "input-selection-background": "#275873",
        "footer-background": BACKGROUND,
    },
)

"""
Bee Tent Maps - design-system constants (redesign v2).

Single source of truth for the map-canvas overlay colors, the accent /
neutral palette, spacing and corner-radius scale. CTk *widget* colors live
in bee_tent_maps_theme.json; the values below are for things you draw
yourself on the satellite map canvas (pins, circles, lines) and for any
place you need a hex string in code.

Nothing here changes app behaviour - these are appearance constants only.
Swap a value and every overlay/label that references it updates.
"""

# --- Map overlay colors (tuned for contrast over dark satellite imagery) ---
# Keep these bright + saturated; each pairs a fill with a dark outline so it
# reads on any imagery. Shown in the on-map legend in the same order.
OVERLAY = {
    "shelter":       "#FFCE3A",   # yellow pin  (outline: #1A1A1A)
    "shelter_edge":  "#1A1A1A",
    "pivot_point":   "#F5453D",   # red center dot
    "pivot_track":   "#FF8A2B",   # orange concentric tracks (dashed)
    "male_bay":      "#2E9BF0",   # blue bands  (was green - changed for contrast)
    "sprayer":       "#FF5A52",   # red boundary outline (dashed)
    "crew_route":    "#A06BFF",   # purple travel line
    "home_depot":    "#2F7FE6",   # blue home / depot marker
}

# --- Interface palette (light shell) ---
PAPER       = "#F4F1EA"   # app background
SURFACE     = "#FFFFFF"   # panels / cards
SURFACE_ALT = "#FBF9F5"   # subtle raised / header rows
BORDER      = "#E4DFD4"   # dividers
BORDER_STRONG = "#D8D2C4" # input borders

INK   = "#221F1A"         # primary text
INK_2 = "#5C564B"         # secondary text
INK_3 = "#938C7E"         # muted / labels
INK_4 = "#B4AD9E"         # faint captions

# --- Accent (honey) + states ---
ACCENT        = "#B87514"   # primary action
ACCENT_HOVER  = "#9E6410"
ACCENT_TINT   = "#FBF1DD"   # active / selected background
ACCENT_TINTBD = "#E7C98A"   # active / selected border
ACCENT_INK    = "#6B4A0E"   # text on tint

# --- Semantic ---
PROFIT  = "#1FA463"   # positive / margin
WARNING = "#D9822B"   # needs-data badge (paired bg #FCF3E7, border #F2DFC2)
DANGER  = "#C4433B"   # destructive (paired border #EAD3D1)

# --- Financial category accents (cost groups) ---
CAT_ITEMS    = "#B87514"
CAT_BEES     = "#E0951F"
CAT_CHEMICAL = "#C4433B"
CAT_FUEL     = "#5C564B"
CAT_LABOUR   = "#127C77"

# --- Spacing scale (px) - 4px base ---
SPACE = (4, 8, 12, 16, 24, 32)

# --- Corner radius ---
RADIUS_INPUT  = 9    # entries, dropdowns, buttons
RADIUS_CARD   = 12   # panels / cards
RADIUS_PILL   = 1000 # switches, scrollbars, progress

# --- Control sizing (comfortable density) ---
CONTROL_HEIGHT = 40  # entries / buttons
CHIP_HEIGHT    = 36  # layer toggles / tool chips
PANEL_PADDING  = 18

"""Shared palette across zones so the lab reads as one place.

Gru's lab base is metallic grey-blue. Each zone gets a distinct accent:
  sports    — green turf, red/yellow balls
  sleeping  — navy + pink pajama stripes
  machine   — exposed steel + hot magenta coils (hero zone)
  kitchen   — cream cabinets, chrome, bubbling green
  bathrooms — tile teal + porcelain white
"""

from pyasset import hex_to_rgb

# Global lab background
FLOOR_BASE = hex_to_rgb("#4a5568")       # dark steel
FLOOR_DARK = hex_to_rgb("#2d3748")
WALL_BASE = hex_to_rgb("#6b7583")        # mid steel, cool
WALL_TRIM = hex_to_rgb("#f2b441")        # gru-yellow trim
PIPE_GREY = hex_to_rgb("#8b95a3")
CEILING = hex_to_rgb("#1a1d24")
RIVET = hex_to_rgb("#2a2f3a")

# Signage-bright
GRU_YELLOW = hex_to_rgb("#ffc933")
GRU_ORANGE = hex_to_rgb("#ff7a1a")
GRU_PURPLE = hex_to_rgb("#4a2b7a")

# Minion palette
MINION_YELLOW = hex_to_rgb("#ffd55e")
MINION_YELLOW_SHADE = hex_to_rgb("#e8b02f")
DENIM_BLUE = hex_to_rgb("#3d7cb8")
DENIM_DARK = hex_to_rgb("#2a5d93")
GOGGLE_SILVER = hex_to_rgb("#c8ced4")
GOGGLE_GLASS = hex_to_rgb("#ffffff")
PUPIL_BROWN = hex_to_rgb("#3a2416")
GLOVE_BLACK = hex_to_rgb("#1e1e1e")
BOOT_BLACK = hex_to_rgb("#141414")
SKIN_DARK = hex_to_rgb("#d39728")

# Sports zone
TURF_GREEN = hex_to_rgb("#4aa048")
TURF_LINE = hex_to_rgb("#e7e7d6")
BALL_RED = hex_to_rgb("#d42a2a")
BALL_YELLOW = hex_to_rgb("#f5d32a")
BALL_WHITE = hex_to_rgb("#f0f0f0")
BALL_BLACK = hex_to_rgb("#1a1a1a")
GOAL_WHITE = hex_to_rgb("#e8eaed")

# Sleeping
PAJAMA_NAVY = hex_to_rgb("#1f2a60")
PAJAMA_STRIPE = hex_to_rgb("#f2b7c7")
MATTRESS_CREAM = hex_to_rgb("#e9e2c8")
BUNK_WOOD = hex_to_rgb("#7a4d2a")
PILLOW = hex_to_rgb("#f6d3dd")

# Machine room — hot, sci-fi
STEEL_LIGHT = hex_to_rgb("#9aa4b3")
STEEL_DARK = hex_to_rgb("#3c4452")
COIL_MAGENTA = hex_to_rgb("#ff2b88")
COIL_CYAN = hex_to_rgb("#2be0ff")
GLOW_CORE = hex_to_rgb("#fff5b0")
PLATFORM_YELLOW = hex_to_rgb("#f2b441")
WARN_STRIPE = hex_to_rgb("#1a1a1a")

# Kitchen
CAB_CREAM = hex_to_rgb("#efe4c6")
CAB_TRIM = hex_to_rgb("#a07842")
CHROME = hex_to_rgb("#c6cfda")
COUNTER_GREY = hex_to_rgb("#d4d7d1")
GOOP_GREEN = hex_to_rgb("#7bd64a")
FLAME_RED = hex_to_rgb("#ff4527")
POT_BLACK = hex_to_rgb("#25262a")

# Bathroom
TILE_TEAL = hex_to_rgb("#3aa4a6")
TILE_LIGHT = hex_to_rgb("#a8dcdb")
TILE_GROUT = hex_to_rgb("#2a2a2a")
STALL_PINK = hex_to_rgb("#f59bb0")
PORCELAIN = hex_to_rgb("#f6f6f2")
WATER_BLUE = hex_to_rgb("#55b8dc")

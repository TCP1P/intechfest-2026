"""sRGB hex -> linear float RGB for glTF vertex colors.

glTF COLOR_0 is linear in the spec; Bevy's StandardMaterial treats vertex
colors as linear by default. We store per-vertex colors as floats.
"""

from __future__ import annotations

import numpy as np


def _srgb_to_linear(c: float) -> float:
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def hex_to_rgb(h: str | int) -> tuple[float, float, float]:
    if isinstance(h, str):
        h = h.lstrip("#")
        v = int(h, 16)
    else:
        v = int(h)
    r = ((v >> 16) & 0xFF) / 255.0
    g = ((v >> 8) & 0xFF) / 255.0
    b = (v & 0xFF) / 255.0
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b))


def rgb(r: float, g: float, b: float) -> tuple[float, float, float]:
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b))


def darken(c, amount=0.7):
    return (c[0] * amount, c[1] * amount, c[2] * amount)


def lighten(c, amount=0.3):
    return (
        min(1.0, c[0] + (1 - c[0]) * amount),
        min(1.0, c[1] + (1 - c[1]) * amount),
        min(1.0, c[2] + (1 - c[2]) * amount),
    )


def to_np(c) -> np.ndarray:
    return np.array([c[0], c[1], c[2]], dtype=np.float32)

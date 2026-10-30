"""Prose display defaults and validation, independent of Qt and project data."""

import math

RECOMMENDED_PROSE_FONT_SIZE = 18
RECOMMENDED_PROSE_LINE_SPACING = 1.8
LEGACY_PROSE_FONT_SIZE = 16
LEGACY_PROSE_LINE_SPACING = 1.0


def prose_font_size(value, default=RECOMMENDED_PROSE_FONT_SIZE):
    if isinstance(value, bool):
        return default
    try:
        return max(12, min(36, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def prose_line_spacing(value, default=RECOMMENDED_PROSE_LINE_SPACING):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
        return round(max(1.0, min(2.4, number)), 1) if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default

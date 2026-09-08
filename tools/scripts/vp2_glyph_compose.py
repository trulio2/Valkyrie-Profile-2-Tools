#!/usr/bin/env python3
"""Stack an accent mark onto a letter, in the letter's own face."""

WIDTH, HEIGHT, PITCH = 32, 28, 32
GLYPH_BYTES = PITCH // 2 * HEIGHT

MARK_INK_FLOOR = 3

MARK_OUTLINE_ROWS = 1

DEFAULT_CLEARANCE = -1


def unpack(block):
    """4-bit packed glyph -> HEIGHT x WIDTH grid of 0..15."""
    return [[(block[y * (PITCH // 2) + x // 2] & 0x0F) if x % 2 == 0
             else (block[y * (PITCH // 2) + x // 2] >> 4)
             for x in range(WIDTH)] for y in range(HEIGHT)]


def pack(grid):
    out = bytearray(GLYPH_BYTES)
    for y in range(HEIGHT):
        for x in range(0, WIDTH, 2):
            out[y * (PITCH // 2) + x // 2] = ((grid[y][x] & 0x0F)
                                              | ((grid[y][x + 1] & 0x0F) << 4))
    return bytes(out)


def ink_rows(grid):
    return [y for y in range(HEIGHT) if any(grid[y])]


def ink_columns(grid, rows):
    columns = [x for y in rows for x in range(WIDTH) if grid[y][x]]
    return (min(columns), max(columns)) if columns else (0, 0)


def _added_ink(plain, accented, y):
    return sum(max(0, accented[y][x] - plain[y][x]) for x in range(WIDTH))


def isolate_mark(plain, accented):
    """Rows an above-the-letter mark occupies in ``accented``."""
    inked = ink_rows(accented)
    if not inked:
        return []
    rows = []
    for y in range(inked[0], HEIGHT):
        if _added_ink(plain, accented, y) < MARK_INK_FLOOR:
            break
        rows.append(y)
    if not rows:
        return []
    for offset in range(1, MARK_OUTLINE_ROWS + 1):
        y = rows[-1] + offset
        if y < HEIGHT and any(accented[y]):
            rows.append(y)
    return rows


def isolate_mark_below(plain, accented):
    """Rows a below-the-letter mark occupies -- the cedilla, in this set."""
    inked = ink_rows(accented)
    if not inked:
        return []
    rows = []
    for y in range(inked[-1], -1, -1):
        if _added_ink(plain, accented, y) < MARK_INK_FLOOR:
            break
        rows.append(y)
    if not rows:
        return []
    for offset in range(1, MARK_OUTLINE_ROWS + 1):
        y = rows[-1] - offset
        if y >= 0 and any(accented[y]):
            rows.append(y)
    return sorted(rows)


def _place(body_block, mark_grid, mark_rows, dy_for, dx_shift=0):
    body = unpack(body_block)
    rows = ink_rows(body)
    if not rows:
        raise ValueError("the base letter has no ink")
    body_left, body_right = ink_columns(body, rows)
    mark_left, mark_right = ink_columns(mark_grid, mark_rows)
    dx = int(round((body_left + body_right) / 2
                   - (mark_left + mark_right) / 2 + dx_shift))
    dx = max(-mark_left, min(dx, WIDTH - 1 - mark_right))
    dy = dy_for(rows, mark_rows)
    out = [list(row) for row in body]
    for y in mark_rows:
        target_y = y + dy
        if not 0 <= target_y < HEIGHT:
            raise ValueError("the mark does not fit in the cell")
        for x in range(WIDTH):
            if mark_grid[y][x]:
                target_x = x + dx
                if not 0 <= target_x < WIDTH:
                    raise ValueError("the mark does not fit beside the body")
                out[target_y][target_x] = max(out[target_y][target_x],
                                              mark_grid[y][x])
    return pack(out)


def compose(body_block, mark_grid, mark_rows, clearance=DEFAULT_CLEARANCE,
            dx_shift=0):
    """Stack ``mark`` over ``body``, centred, with ``clearance`` rows of air."""
    return _place(body_block, mark_grid, mark_rows,
                  lambda rows, mark: rows[0] - mark[-1] - 1 - clearance,
                  dx_shift)


def compose_below(body_block, mark_grid, mark_rows,
                  clearance=DEFAULT_CLEARANCE):
    """Hang ``mark`` under ``body``, centred, with ``clearance`` rows of air."""
    return _place(body_block, mark_grid, mark_rows,
                  lambda rows, mark: rows[-1] - mark[0] + 1 + clearance)


def compose_below_baseline(body_block, mark_grid, mark_rows, donor_bottom):
    """Hang ``mark`` under ``body`` at the baseline it was isolated against."""
    return _place(body_block, mark_grid, mark_rows,
                  lambda rows, _mark: rows[-1] - donor_bottom)


BODY_START = {"i": 13}


def compose_replace(body_block, mark_grid, mark_rows, body_from,
                    clearance=DEFAULT_CLEARANCE, dx_shift=0):
    """Put ``mark`` where the body's own top used to be."""
    body = unpack(body_block)
    stripped = [([0] * WIDTH) if y < body_from else list(row)
                for y, row in enumerate(body)]
    if not ink_rows(stripped):
        raise ValueError("stripping the body's top left no letter")
    return _place(pack(stripped), mark_grid, mark_rows,
                  lambda rows, mark: rows[0] - mark[-1] - 1 - clearance,
                  dx_shift)


COMPOSITES = {
    "á": ("a", "á", "above"),
    "à": ("a", "à", "above"),
    "â": ("a", "â", "above"),
    "ã": ("a", "ã", "above"),
    "é": ("e", "é", "above"),
    "ê": ("e", "ê", "above"),
    "í": ("i", "í", "above"),
    "ó": ("o", "ó", "above"),
    "ô": ("o", "ô", "above"),
    "õ": ("o", "õ", "above"),
    "ú": ("u", "ú", "above"),
    "ü": ("u", "ü", "above"),
    "ç": ("c", "ç", "below"),
    "Á": ("A", "á", "above"),
    "À": ("A", "à", "above"),
    "Â": ("A", "â", "above"),
    "Ã": ("A", "ã", "above"),
    "É": ("E", "é", "above"),
    "Ê": ("E", "ê", "above"),
    "Í": ("I", "é", "above"),
    "Ó": ("O", "ó", "above"),
    "Ô": ("O", "ô", "above"),
    "Õ": ("O", "õ", "above"),
    "Ú": ("U", "ú", "above"),
    "Ü": ("U", "ü", "above"),
    "Ç": ("C", "ç", "below"),
    "ä": ("a", "ü", "above"),
    "Ä": ("A", "ü", "above"),
    "ë": ("e", "ü", "above"),
    "Ë": ("E", "ü", "above"),
    "ö": ("o", "ü", "above"),
    "Ö": ("O", "ü", "above"),
    "è": ("e", "à", "above"),
    "È": ("E", "à", "above"),
    "ò": ("o", "à", "above"),
    "Ò": ("O", "à", "above"),
    "ù": ("u", "à", "above"),
    "Ù": ("U", "à", "above"),
    "û": ("u", "â", "above"),
    "Û": ("U", "â", "above"),
    "ñ": ("n", "ã", "above"),
    "Ñ": ("N", "ã", "above"),
    "ì": ("i", "à", "replace"),
    "í": ("i", "é", "replace"),
    "î": ("i", "â", "replace"),
    "ï": ("i", "ü", "replace"),
    "Ì": ("I", "à", "above"),
    "Î": ("I", "â", "above"),
    "Ï": ("I", "ü", "above"),
    "å": ("a", "å", "above"),
    "Å": ("A", "å", "above"),
}

DONOR_BASE = {
    "à": "a", "á": "a", "â": "a", "ã": "a",
    "é": "e", "ê": "e",
    "í": "i",
    "ó": "o", "ô": "o", "õ": "o",
    "ú": "u", "ü": "u",
    "ç": "c",
    "å": "a",
}

MARK_VERTICAL_SHIFTS = {"ã": -1, "õ": -1,
                        "á": -1, "é": -1, "ó": -1, "ú": -1,
                        "à": -1}

MARK_HORIZONTAL_SHIFTS = {"á": 2, "é": 2, "ó": 2, "ú": 2, "à": -1.5,
                          "ã": -0.5, "õ": -0.5}


LOWERCASE_EXTRA_OVERLAP = 1

NO_LOWERCASE_OVERLAP = frozenset({"å"})


def compose_character(body_block, character, mark_grid, mark_rows,
                      donor_bottom=None, body_from=None):
    """Compose ``character`` with its recipe-specific placement.

    ``body_from`` overrides a replacement recipe's face-specific boundary.
    """
    base, donor, position = COMPOSITES[character]
    shift = MARK_VERTICAL_SHIFTS.get(donor, 0)
    sideways = MARK_HORIZONTAL_SHIFTS.get(donor, 0)
    if base.islower() and donor not in NO_LOWERCASE_OVERLAP:
        shift += LOWERCASE_EXTRA_OVERLAP
    if position == "replace":
        return compose_replace(body_block, mark_grid, mark_rows,
                               (BODY_START[base] if body_from is None
                                else body_from), clearance=1,
                               dx_shift=sideways)
    if position == "below":
        if donor_bottom is not None:
            return compose_below_baseline(body_block, mark_grid, mark_rows,
                                          donor_bottom)
        return compose_below(body_block, mark_grid, mark_rows,
                             clearance=DEFAULT_CLEARANCE + shift)
    return compose(body_block, mark_grid, mark_rows,
                   clearance=DEFAULT_CLEARANCE - shift, dx_shift=sideways)


def codepage_byte(letter):
    """The shared-codepage byte that draws ``letter``: ASCII - 0x1F."""
    return ord(letter) - 0x1F

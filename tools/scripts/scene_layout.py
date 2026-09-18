"""Measured wrapping and pagination for scene dialogue."""

import re
import textwrap

from .scene_codec import pack_tokens
from .vp2_scene_fingerprint import PAGE_BREAK, PAGE_BREAK_TEXT
from .vp2_cutscene_subtitles import (
    CODEPAGE_TOKENS, FRAGMENT_MARKER, HARD_BREAK_TEXT, RECORD_PARAMETERS,
    TEXT_BREAKS, apply_hard_breaks,
)


def render_raw_tokens(*args, **kwargs):
    from .vp2_cutscene_subtitles import render_raw_tokens as implementation
    return implementation(*args, **kwargs)


def fragment_target(*args, **kwargs):
    from .vp2_cutscene_subtitles import fragment_target as implementation
    return implementation(*args, **kwargs)


SUBTITLE_MAX_WIDTH = 435

SUBTITLE_MAX_LINES = 3

NPC_DIALOGUE_DISPLAY_TYPE = 0

NPC_DIALOGUE_MAX_LINES = 2

TEXT_RUN_END = 0x8082

NPC_PAGE_SEPARATOR = "<%04X>%s" % (TEXT_RUN_END, PAGE_BREAK_TEXT)

def dialogue_max_lines(display_types):
    """Line capacity of the ECS consumer(s) drawing one message."""
    return (NPC_DIALOGUE_MAX_LINES
            if set(display_types or ()) == {NPC_DIALOGUE_DISPLAY_TYPE}
            else SUBTITLE_MAX_LINES)


def record_owns_authored_layout(runs, max_lines):
    if max_lines != NPC_DIALOGUE_MAX_LINES:
        return False
    pages = 0
    rows = 1
    for _start, _end, _visible, _text, tokens in runs:
        pages += tokens.count(PAGE_BREAK)
        rows += tokens.count(TEXT_BREAKS[0])
    return pages == 0 and rows > max_lines

def glyph_advances(expanded, metric_start, alphabet):
    """``character -> pixel advance``, read from the metric beside each slot."""
    advances = {}
    for slot in sorted(alphabet):
        character = alphabet[slot]
        if character is not None:
            advances.setdefault(character, expanded[metric_start + slot * 2])
    return advances

def wrap_to_width(text, advances, limit=SUBTITLE_MAX_WIDTH,
                  max_lines=SUBTITLE_MAX_LINES, label=None,
                  auto_paginate=False, soft_breaks=""):
    """Break ``text`` into the fewest lines that fit, then even them out."""
    fallback = advances.get(".", 8)
    measure = lambda part: sum(advances.get(c, fallback) for c in part)

    def greedy(width):
        if soft_breaks:
            separators = set(soft_breaks) | {" "}
            units, prefix, word = [], "", ""
            for character in text:
                if character in separators:
                    if word:
                        units.append((prefix, word))
                        prefix, word = "", ""
                    prefix += character
                else:
                    word += character
            if word or prefix:
                units.append((prefix, word))
            lines, current = [], ""
            for prefix, word in units:
                trial = current + prefix + word
                if not current or measure(trial) <= width:
                    current = trial
                    continue
                lines.append(current)
                current = "".join(character for character in prefix
                                  if character in soft_breaks) + word
            if current:
                lines.append(current)
            return lines
        lines, current = [], ""
        for word in text.split(" "):
            trial = word if not current else current + " " + word
            if not current or measure(trial) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    if measure(text) <= limit:
        return text
    lines = greedy(limit)
    if len(lines) > max_lines and not auto_paginate:
        raise ValueError(
            "%s needs %d lines at %d px; the subtitle box holds %d. Shorten it."
            % (label or repr(text[:40]), len(lines), limit, max_lines))
    breakable = text
    for marker in soft_breaks:
        breakable = breakable.replace(marker, " ")
    low, high = max(measure(word) for word in breakable.split(" ")), limit
    while low < high:
        middle = (low + high) // 2
        if len(greedy(middle)) <= len(lines):
            high = middle
        else:
            low = middle + 1
    balanced = greedy(low)
    if auto_paginate and len(balanced) > max_lines:
        pages = ["\n".join(balanced[index:index + max_lines])
                 for index in range(0, len(balanced), max_lines)]
        return NPC_PAGE_SEPARATOR.join(pages)
    return "\n".join(balanced)

def wrap_between_breaks(text, advances, limit=SUBTITLE_MAX_WIDTH,
                        max_lines=SUBTITLE_MAX_LINES, label=None,
                        auto_paginate=False, soft_breaks=""):
    """Wrap each stretch between the breaks the text already carries."""
    segments = text.split("\n")
    wrapped = []
    for segment in segments:
        if not segment.strip():
            # A leading empty segment is the junction break itself, not a
            # blank line to fill.
            wrapped.append(segment)
            continue
        wrapped.append(wrap_to_width(
            segment, advances, limit, max_lines, label,
            auto_paginate=auto_paginate, soft_breaks=soft_breaks))
    joined = "\n".join(wrapped)
    page_lines = [0]
    for line in joined.split("\n"):
        if line.strip() == PAGE_BREAK_TEXT.strip():
            page_lines.append(0)
        elif line.strip():
            page_lines[-1] += 1
    needed = max(page_lines, default=0)
    if needed > max_lines:
        raise ValueError(
            "%s needs %d lines at %d px; the subtitle box holds %d. Shorten "
            "it." % (label or repr(text[:40]), needed, limit, max_lines))
    return joined

def soften_dialogue_breaks(text):
    """Turn inherited interior line wrapping back into ordinary spaces.

    A break marked ``<BR>`` is the translator's own and is kept.
    """
    softened = []
    for index, character in enumerate(text):
        if character != "\n":
            softened.append(character)
            continue
        if index == 0 or index == len(text) - 1 \
                or text[index - 1] == "\n" or text[index + 1] == "\n":
            softened.append(character)
            continue
        before = text[text.rfind("\n", 0, index) + 1:index].strip()
        following_at = text.find("\n", index + 1)
        following = text[index + 1:(None if following_at < 0
                                     else following_at)].strip()
        softened.append("\n" if PAGE_BREAK_TEXT.strip() in (before, following)
                        else " ")
    return apply_hard_breaks("".join(softened))

def wrap_translation(text, source_tokens, advances=None,
                     max_lines=SUBTITLE_MAX_LINES, auto_paginate=False):
    if advances is not None:
        return wrap_between_breaks(
            soften_dialogue_breaks(text), advances, max_lines=max_lines,
            auto_paginate=auto_paginate)
    text = apply_hard_breaks(text)
    if "\n" in text:
        return text
    source_lines = 1 + source_tokens.count(0x8080)
    if source_lines == 1 or len(text) <= 40:
        return text
    width = max(24, min(42, (len(text) + source_lines - 1) // source_lines + 3))
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=False,
                                    break_on_hyphens=False))

STRUCTURED_RUN_BOUNDARY = "\uffff"

def wrap_structured_translations(parts, advances,
                                 max_lines=SUBTITLE_MAX_LINES):
    """Lay out local-font fragments together, then restore their boundaries."""
    if len(parts) < 2:
        return list(parts)
    if any(STRUCTURED_RUN_BOUNDARY in part for part in parts):
        raise ValueError("translation contains the internal run boundary")
    measured = dict(advances)
    measured[STRUCTURED_RUN_BOUNDARY] = 0
    logical = soften_dialogue_breaks(
        STRUCTURED_RUN_BOUNDARY.join(parts))
    wrapped = wrap_between_breaks(
        logical, measured, max_lines=max_lines,
        auto_paginate=(max_lines == NPC_DIALOGUE_MAX_LINES),
        soft_breaks=STRUCTURED_RUN_BOUNDARY)
    result = wrapped.split(STRUCTURED_RUN_BOUNDARY)
    if len(result) != len(parts):
        raise AssertionError("structured dialogue lost a run boundary")
    return result

def verification_dialogue_layout(text, advances, max_lines,
                                 structured_local=False,
                                 record_owns_layout=False):
    if (max_lines != NPC_DIALOGUE_MAX_LINES or record_owns_layout):
        return None
    rendered = render_raw_tokens(text.strip())
    if FRAGMENT_MARKER not in rendered:
        layout = wrap_translation(
            rendered, [], advances, max_lines=max_lines,
            auto_paginate=True)
    else:
        if not structured_local:
            return None
        parts = [fragment_target(part)
                 for part in rendered.split(FRAGMENT_MARKER)]
        layout = (" %s " % FRAGMENT_MARKER).join(
            wrap_structured_translations(
                parts, advances, max_lines=max_lines))
    return layout.replace("<%04X>" % TEXT_RUN_END,
                          " %s " % FRAGMENT_MARKER)

def break_overflowing_run_junction(previous, current, advances,
                                    limit=SUBTITLE_MAX_WIDTH):
    """Start *current* on a new line when two adjacent runs overflow."""
    if not previous or not current or current.startswith("\n"):
        return current
    left = previous.rsplit("\n", 1)[-1]
    right = current.split("\n", 1)[0]
    fallback = advances.get(".", 8)
    width = sum(advances.get(character, fallback)
                for character in left + right)
    if width <= limit:
        return current
    match = re.match(r"([.,:;!?]+)([ \t]+)(.*)", current, re.DOTALL)
    if match:
        punctuation, _spacing, tail = match.groups()
        punctuated = sum(advances.get(character, fallback)
                         for character in left + punctuation)
        if punctuated <= limit:
            return punctuation + "\n" + tail
    return "\n" + current

def preserve_source_run_edges(source, target):
    """Carry structural whitespace at a visible run's outer edges."""
    prefix = source[:len(source) - len(source.lstrip(" \t\n"))]
    suffix = source[len(source.rstrip(" \t\n")):]
    if prefix.count("\n") >= 2:
        target = prefix + target.lstrip("\n")
    elif "\n" not in prefix and prefix:
        target = " " + target
    if suffix.count("\n") >= 2:
        target = target.rstrip("\n") + suffix
    elif "\n" not in suffix and suffix:
        target += " "
    return target


def preserve_translated_run_spacing(previous, source, raw_target, target):
    """Keep an authored word space when a source newline is reflowed away."""
    prefix = source[:len(source) - len(source.lstrip(" \t\n"))]
    authored = raw_target[:len(raw_target) - len(raw_target.lstrip(" \t\n\r"))]
    if (prefix.count("\n") != 1
            or not authored
            or "\n" in authored or "\r" in authored
            or not previous or not target
            or previous[-1].isspace() or target[0].isspace()
            or not previous[-1].isalnum() or not target[0].isalnum()):
        return target
    return " " + target


def _record_gap_tokens(data):
    """Decode a run gap while skipping control parameters."""
    tokens = []
    position = 0
    while position < len(data):
        first = data[position]
        position += 1
        if first >= 0x80:
            if position >= len(data):
                raise ValueError("record gap ends inside a two-byte token")
            token = first | (data[position] << 8)
            position += 1
        else:
            token = first
        tokens.append(token)
        parameter_bytes = RECORD_PARAMETERS.get(token, 0)
        if position + parameter_bytes > len(data):
            raise ValueError("record gap ends inside a control parameter")
        position += parameter_bytes
    return tokens

def preserve_input_icon_spacing(target, leading_gap=b"", trailing_gap=b""):
    """Keep an inline 0x8099 input icon separated from adjacent text."""
    icon = 0x8099
    boundaries = set(TEXT_BREAKS) | {TEXT_RUN_END, 0}
    leading_tokens = _record_gap_tokens(leading_gap)
    trailing_tokens = _record_gap_tokens(trailing_gap)

    leading_icons = [index for index, token in enumerate(leading_tokens)
                     if token == icon]
    icon_immediately_before = bool(leading_icons) and not any(
        token in boundaries for token in leading_tokens[leading_icons[-1] + 1:])
    trailing_icons = [index for index, token in enumerate(trailing_tokens)
                      if token == icon]
    icon_immediately_after = bool(trailing_icons) and not any(
        token in boundaries for token in trailing_tokens[:trailing_icons[0]])

    if icon_immediately_before and target and not target[0].isspace():
        target = " " + target
    if icon_immediately_after and target and not target[-1].isspace():
        target += " "
    return target

def materialize_blank_line(tokens):
    """Make a bare double break advance an actual empty tutorial row."""
    if tokens == [0x8080, 0x8080]:
        return pack_tokens([0x8080, CODEPAGE_TOKENS[" "],
                            CODEPAGE_TOKENS[" "], 0x8080])[:-1]
    return None

# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Audio tracks inside VP2's XOR-protected MPEG program streams."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import struct
import wave

from ..scripts.paths import DATA_DIR


PAD_BYTES = 2048
XOR_CHUNK = 4 * 1024 * 1024
PCM_CODEC = 0
TAC_CODEC = 1
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
PCM_FRAME_BYTES = CHANNELS * SAMPLE_WIDTH
PRIVATE_PREFIX = b"\xff\x90\x00"
DISC_UNIT = 0x1000
DISC_AUDIO_MARKER_OFFSET = 0x16
DISC_WRAPPER_OFFSET = 0x20
DISC_DATA_OFFSET = 0x25
DISC_NEXT_HEADER_OFFSET = 0x0E
DISC_AUDIO_MARKER = b"\x00\xde"
DISC_WRAPPER = b"\x00\x00\x91\x00"
DISC_PES_STREAM = 0xCE
DISC_PES_WRAPPER = b"\x01\x91\x00"
SCENE_RESOURCES = {11: 10, 14: 1337, 20: 1323}
MOVIE_NAME = re.compile(
    r"^fmv-(?P<entry>\d{4})-(?P<movie>\d{3})\.wav$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AudioSpan:
    offset: int
    length: int


@dataclass(frozen=True)
class AudioTrack:
    movie: int
    codec: int
    spans: tuple[AudioSpan, ...]
    data: bytes

    @property
    def capacity(self):
        return sum(span.length for span in self.spans)


@dataclass(frozen=True)
class TacInfo:
    samples: int
    stream_size: int


def load_xor_pad(path=None) -> bytes:
    """Load the region-invariant 2048-byte movie XOR pad."""
    path = Path(path or DATA_DIR / "fmv-xor-pad.txt")
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ValueError("cannot read FMV XOR pad %s: %s" % (path, exc)) from exc
    values = re.findall(
        r"\b[0-9a-fA-F]{2}\b",
        "\n".join(line for line in lines if not line.lstrip().startswith("#")),
    )
    pad = bytes(int(value, 16) for value in values)
    if len(pad) != PAD_BYTES:
        raise ValueError(
            "FMV XOR pad must contain %d bytes (found %d)"
            % (PAD_BYTES, len(pad))
        )
    return pad


def xor_bytes(data: bytes, pad: bytes, start=0) -> bytes:
    """Apply the repeating movie XOR pad without a per-byte Python loop."""
    if len(pad) != PAD_BYTES:
        raise ValueError("FMV XOR pad must be exactly %d bytes" % PAD_BYTES)
    output = bytearray(len(data))
    for offset in range(0, len(data), XOR_CHUNK):
        chunk = data[offset:offset + XOR_CHUNK]
        phase = (start + offset) % len(pad)
        rotated = pad[phase:] + pad[:phase]
        key = (rotated * ((len(chunk) + len(pad) - 1) // len(pad)))[
            :len(chunk)
        ]
        output[offset:offset + len(chunk)] = (
            int.from_bytes(chunk, "little") ^ int.from_bytes(key, "little")
        ).to_bytes(len(chunk), "little")
    return bytes(output)


def _packet_end(data, offset):
    if offset + 6 > len(data):
        return None
    end = offset + 6 + int.from_bytes(data[offset + 4:offset + 6], "big")
    return end if end <= len(data) else None


def _looks_like_tac_header(data: bytes) -> bool:
    if len(data) < 0x20:
        return False
    info_offset = struct.unpack_from("<I", data, 0x00)[0]
    frame_count, frame_last = struct.unpack_from("<HH", data, 0x0C)
    stream_size = struct.unpack_from("<I", data, 0x14)[0]
    return (
        0x20 <= info_offset <= 0x4E000 and frame_count > 0 and
        frame_last < 1024 and stream_size > 0 and
        stream_size % 0x4E000 == 0
    )


def parse_audio_tracks(clear: bytes) -> tuple[AudioTrack, ...]:
    """Parse protected-disc and runtime PES audio into codec streams."""
    packets = []
    cursor = 0
    prefix = b"\x00\x00\x01"
    while True:
        offset = clear.find(prefix, cursor)
        if offset < 0 or offset + 4 > len(clear):
            break
        code = clear[offset + 3]
        if code == 0xBA:  # MPEG-2 pack header
            if offset + 14 > len(clear):
                break
            cursor = offset + 14 + (clear[offset + 13] & 0x07)
            continue
        if code == 0xB9:  # program end
            cursor = offset + 4
            continue
        if code == 0xBD or code == 0xBB or code == 0xBC or code == 0xBE or \
                code == 0xBF or 0xC0 <= code <= 0xEF:
            end = _packet_end(clear, offset)
            if end is None:
                break
            if (code in (0xBD, DISC_PES_STREAM) and offset + 9 <= end and
                    clear[offset + 6] & 0xC0 == 0x80):
                payload = offset + 9 + clear[offset + 8]
                if (code == 0xBD and payload + 4 <= end and
                        clear[payload:payload + 3] == PRIVATE_PREFIX):
                    codec = clear[payload + 3]
                    data_offset = payload + 4
                elif (code == DISC_PES_STREAM and payload + 4 <= end and
                        clear[payload:payload + 3] == DISC_PES_WRAPPER):
                    codec = clear[payload + 3]
                    data_offset = payload + 4
                else:
                    codec = None
                    data_offset = end
                if codec is not None:
                    if codec in (PCM_CODEC, TAC_CODEC):
                        span = AudioSpan(data_offset, end - data_offset)
                        packets.append((codec, span))
            cursor = end
            continue
        cursor = offset + 4

    for base in range(0, len(clear) - DISC_UNIT, DISC_UNIT):
        marker = base + DISC_AUDIO_MARKER_OFFSET
        wrapper = base + DISC_WRAPPER_OFFSET
        if (clear[marker:marker + 2] != DISC_AUDIO_MARKER or
                clear[wrapper:wrapper + 4] != DISC_WRAPPER):
            continue
        codec = clear[wrapper + 4]
        if codec not in (PCM_CODEC, TAC_CODEC):
            continue
        data_offset = base + DISC_DATA_OFFSET
        end = min(
            base + DISC_UNIT + DISC_NEXT_HEADER_OFFSET,
            len(clear),
        )
        packets.append((codec, AudioSpan(data_offset, end - data_offset)))

    packets.sort(key=lambda packet: packet[1].offset)

    groups = []
    for codec, span in packets:
        starts_tac = (
            codec == TAC_CODEC and
            _looks_like_tac_header(clear[span.offset:span.offset + span.length])
        )
        if not groups or groups[-1][0] != codec or (
                starts_tac and groups[-1][1]):
            groups.append((codec, []))
        groups[-1][1].append(span)

    tracks = []
    for movie_index, (codec, spans) in enumerate(groups):
        payload = b"".join(
            clear[span.offset:span.offset + span.length] for span in spans
        )
        if codec == PCM_CODEC:
            complete = len(payload) // PCM_FRAME_BYTES * PCM_FRAME_BYTES
            payload = payload[:complete]
        track = AudioTrack(movie_index, codec, tuple(spans), payload)
        tracks.append(track)
    return tuple(tracks)


def replace_pcm_track(clear: bytes, track: AudioTrack, pcm: bytes) -> bytes:
    """Replace one PCM soundtrack without changing any PES packet geometry."""
    if track.codec != PCM_CODEC:
        raise ValueError("TAC movie audio cannot be replaced from WAV")
    if len(pcm) % PCM_FRAME_BYTES:
        raise ValueError("movie PCM must contain complete stereo frames")
    if len(pcm) > track.capacity:
        raise ValueError(
            "movie audio needs %d bytes but its packet slots hold %d"
            % (len(pcm), track.capacity)
        )
    fitted = pcm + bytes(track.capacity - len(pcm))
    rebuilt = bytearray(clear)
    consumed = 0
    for span in track.spans:
        rebuilt[span.offset:span.offset + span.length] = fitted[
            consumed:consumed + span.length
        ]
        consumed += span.length
    return bytes(rebuilt)


def exported_filename(entry, movie, suffix="wav"):
    return "fmv-%04d-%03d.%s" % (entry, movie, suffix)


def parse_exported_filename(path):
    match = MOVIE_NAME.fullmatch(Path(path).name)
    if not match:
        return None
    return int(match.group("entry")), int(match.group("movie"))


def read_pcm_wav(path) -> bytes:
    path = Path(path)
    try:
        with wave.open(str(path), "rb") as source:
            shape = (
                source.getnchannels(), source.getsampwidth(),
                source.getframerate(), source.getcomptype(),
            )
            expected = (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, "NONE")
            if shape != expected:
                raise ValueError(
                    "%s must be uncompressed 16-bit stereo PCM at %d Hz "
                    "(found %d channel(s), %d-bit, %d Hz, %s)"
                    % (path.name, SAMPLE_RATE, shape[0], shape[1] * 8,
                       shape[2], shape[3])
                )
            return source.readframes(source.getnframes())
    except wave.Error as exc:
        raise ValueError("cannot read WAV %s: %s" % (path, exc)) from exc


def write_pcm_wav(path, pcm: bytes) -> None:
    path = Path(path)
    if len(pcm) % PCM_FRAME_BYTES:
        raise ValueError("movie PCM payload is not stereo-frame aligned")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(SAMPLE_WIDTH)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)


def tac_info(data: bytes):
    """Return duration metadata for a complete tri-Ace TAC stream."""
    if not _looks_like_tac_header(data):
        return None
    frame_count, frame_last = struct.unpack_from("<HH", data, 0x0C)
    stream_size = struct.unpack_from("<I", data, 0x14)[0]
    if len(data) > stream_size or len(data) < stream_size - 0x4E000:
        return None
    return TacInfo((frame_count - 1) * 1024 + frame_last + 1, stream_size)

# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Audio tracks inside VP2's protected MPEG program streams."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zlib

MOVIE_WORD_BYTES = 8
MOVIE_BLOCK_BYTES = 64
MOVIE_STATE_MASK = 0x1F
MOVIE_WORD_MASK = (1 << 64) - 1
MOVIE_KEYS = (
    0x84DFEFF0DD23524E,
    0x0A3A59BA1F2723B3,
    0xF2044E20DDAB1AAB,
    0x192EDBB7946026BF,
    0x93C21E3A70122574,
    0xB1C25EB6795DF4A1,
    0x84D9E6C51A667F25,
    0x0A25597A06E9E7B3,
)
PCM_CODEC = 0
TAC_CODEC = 1
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
PCM_FRAME_BYTES = CHANNELS * SAMPLE_WIDTH
PCM_CHANNEL_BLOCK_SAMPLES = 1024
TAC_FRAME_SAMPLES = 1024
TAC_FRAME_SECONDS = TAC_FRAME_SAMPLES / SAMPLE_RATE
PCM_LAYOUT_BLOCK_SAMPLES = CHANNELS * PCM_CHANNEL_BLOCK_SAMPLES
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
SCENE_RESOURCES = {
    11: 10,
    14: 1337,
    15: 1337,
    16: 1337,
    18: 1337,
    20: 1323,
}
MOVIE_NAME = re.compile(
    r"^fmv-(?P<entry>\d{4})-(?P<movie>\d{3})\.(?:wav|laac)$",
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


def _transform_protected_movie(data: bytes, start: int, decode: bool) -> bytes:
    """Apply the reversible EE movie transform to 8-byte-aligned data."""
    if start < 0 or start % MOVIE_WORD_BYTES:
        raise ValueError("protected movie offset must be a non-negative multiple of 8")
    complete = len(data) // MOVIE_WORD_BYTES * MOVIE_WORD_BYTES
    words = array("Q")
    words.frombytes(data[:complete])
    if sys.byteorder != "little":
        words.byteswap()
    first_word = start // MOVIE_WORD_BYTES
    for index, word in enumerate(words):
        absolute_word = first_word + index
        key = MOVIE_KEYS[absolute_word % len(MOVIE_KEYS)]
        adjustment = 57 + (
            ((absolute_word * MOVIE_WORD_BYTES) // MOVIE_BLOCK_BYTES) &
            MOVIE_STATE_MASK
        )
        if decode:
            transformed = ((word ^ key) - adjustment) & MOVIE_WORD_MASK
        else:
            transformed = ((word + adjustment) & MOVIE_WORD_MASK) ^ key
        words[index] = transformed
    if sys.byteorder != "little":
        words.byteswap()
    output = bytearray(words.tobytes())
    if complete < len(data):
        absolute_word = first_word + len(words)
        word = int.from_bytes(data[complete:], "little")
        key = MOVIE_KEYS[absolute_word % len(MOVIE_KEYS)]
        adjustment = 57 + ((absolute_word // 8) & MOVIE_STATE_MASK)
        if decode:
            transformed = ((word ^ key) - adjustment) & MOVIE_WORD_MASK
        else:
            transformed = ((word + adjustment) & MOVIE_WORD_MASK) ^ key
        output.extend(transformed.to_bytes(MOVIE_WORD_BYTES, "little")[
            :len(data) - complete
        ])
    return bytes(output)


def decode_protected_movie(data: bytes, start=0) -> bytes:
    """Decode bytes using the transform run by VP2's EE movie player."""
    return _transform_protected_movie(data, start, True)


def encode_protected_movie(data: bytes, start=0) -> bytes:
    """Encode clear movie bytes for storage in a protected VP2 entry."""
    return _transform_protected_movie(data, start, False)


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


def replace_tac_track(clear: bytes, track: AudioTrack, tac: bytes) -> bytes:
    """Replace one encoded TAC soundtrack without changing packet geometry."""
    if track.codec != TAC_CODEC:
        raise ValueError("PCM movie audio cannot be replaced from TAC")
    source_info = tac_info(track.data)
    replacement_info = tac_info(tac)
    if replacement_info is None:
        raise ValueError("replacement is not a complete TAC stream")
    if source_info is None:
        raise ValueError("target movie track is not a complete TAC stream")
    if replacement_info.samples != source_info.samples:
        raise ValueError(
            "replacement TAC has %d samples but the target has %d"
            % (replacement_info.samples, source_info.samples)
        )
    if len(tac) > track.capacity:
        raise ValueError(
            "movie TAC needs %d bytes but its packet slots hold %d"
            % (len(tac), track.capacity)
        )
    fitted = tac + b"\xff" * (track.capacity - len(tac))
    rebuilt = bytearray(clear)
    consumed = 0
    for span in track.spans:
        rebuilt[span.offset:span.offset + span.length] = fitted[
            consumed:consumed + span.length
        ]
        consumed += span.length
    return bytes(rebuilt)


def tac_target_samples(target: bytes) -> int:
    """Validate a destination TAC model and return its duration."""
    target_info = tac_info(target)
    if target_info is None:
        raise ValueError("target movie track is not a complete TAC stream")
    return target_info.samples


def _pcm_array(data: bytes) -> array:
    if len(data) % PCM_FRAME_BYTES:
        raise ValueError("movie PCM must contain complete stereo frames")
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _pcm_bytes(samples: array) -> bytes:
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def decode_pcm_payload(data: bytes) -> bytes:
    """Convert VP2's 1024-sample planar stereo blocks to WAV interleaving."""
    source = _pcm_array(data)
    output = array("h", [0]) * len(source)
    for start in range(0, len(source), PCM_LAYOUT_BLOCK_SAMPLES):
        count = min(PCM_LAYOUT_BLOCK_SAMPLES, len(source) - start)
        frames = count // CHANNELS
        output[start:start + count:2] = source[start:start + frames]
        output[start + 1:start + count:2] = source[
            start + frames:start + count
        ]
    return _pcm_bytes(output)


def encode_pcm_payload(pcm: bytes) -> bytes:
    """Convert interleaved WAV PCM to VP2's 1024-sample planar blocks."""
    source = _pcm_array(pcm)
    output = array("h", [0]) * len(source)
    for start in range(0, len(source), PCM_LAYOUT_BLOCK_SAMPLES):
        count = min(PCM_LAYOUT_BLOCK_SAMPLES, len(source) - start)
        frames = count // CHANNELS
        output[start:start + frames] = source[start:start + count:2]
        output[start + frames:start + count] = source[
            start + 1:start + count:2
        ]
    return _pcm_bytes(output)


def shift_pcm(pcm: bytes, seconds) -> bytes:
    """Shift interleaved PCM while preserving its exact duration."""
    if len(pcm) % PCM_FRAME_BYTES:
        raise ValueError("movie PCM must contain complete stereo frames")
    try:
        shift = round(float(seconds) * SAMPLE_RATE)
    except (TypeError, ValueError) as exc:
        raise ValueError("movie sync must be a number of seconds") from exc
    frames = len(pcm) // PCM_FRAME_BYTES
    if not shift:
        return pcm
    silence = bytes(min(abs(shift), frames) * PCM_FRAME_BYTES)
    if shift > 0:
        return silence + pcm[:max(0, frames - shift) * PCM_FRAME_BYTES]
    amount = min(-shift, frames)
    return pcm[amount * PCM_FRAME_BYTES:] + silence


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


def find_vgmstream_cli():
    """Locate the optional TAC decoder in source and packaged runs."""
    name = "vgmstream-cli.exe" if sys.platform == "win32" else "vgmstream-cli"
    override = os.environ.get("VP2_VGMSTREAM_CLI")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    bundled = root / "vendor" / "vgmstream" / name
    if bundled.is_file():
        return bundled.resolve()
    found = shutil.which(name)
    return Path(found).resolve() if found else None


def _runtime_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def find_tac_encoder():
    """Locate the optional native TAC encoder in source and packaged runs."""
    name = "vp2-tac-encode.exe" if sys.platform == "win32" else "vp2-tac-encode"
    override = os.environ.get("VP2_TAC_ENCODER")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    bundled = _runtime_root() / "vendor" / "tac_encoder" / name
    if bundled.is_file():
        return bundled.resolve()
    found = shutil.which(name)
    return Path(found).resolve() if found else None


def tac_sync_frames(seconds) -> int:
    """Round a signed user offset to the codec's lossless frame boundary."""
    try:
        value = float(seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("movie sync must be a number of seconds") from exc
    if not (-float("inf") < value < float("inf")):
        raise ValueError("movie sync must be finite")
    return round(value * SAMPLE_RATE / TAC_FRAME_SAMPLES)


def tac_sync_seconds(frames: int) -> float:
    return int(frames) * TAC_FRAME_SECONDS


TAC_BAND_LADDER = ("11", "10", "9", "8", "7")
TAC_REDUCED_BANDS = 8


def encode_tac_wav(source, template: bytes, capacity: int, sync_seconds=0,
                   executable=None, target_samples=None,
                   progress=None) -> bytes:
    """Encode one stereo WAV using destination TAC metadata and geometry."""
    source = Path(source)
    pcm = read_pcm_wav(source)
    source_info = tac_info(template)
    if source_info is None:
        raise ValueError("target movie track is not a complete TAC stream")
    target_samples = int(
        source_info.samples if target_samples is None else target_samples
    )
    if target_samples <= 0:
        raise ValueError("target TAC duration must be positive")
    encoder = Path(executable) if executable else find_tac_encoder()
    if encoder is None:
        raise ValueError("the packaged TAC encoder is unavailable")
    runtime = _runtime_root()
    compressed = (
        runtime / "data" / "tac" / "analysis-pair.f32.zlib",
        runtime / "data" / "tac" / "overlap.f32.zlib",
    )
    missing = [str(path) for path in compressed if not path.is_file()]
    if missing:
        raise ValueError("TAC encoder data is missing: %s" % ", ".join(missing))
    sync_frames = tac_sync_frames(sync_seconds)
    with tempfile.TemporaryDirectory(prefix="vp2-tac-") as temporary:
        folder = Path(temporary)
        template_path = folder / "template.laac"
        analysis_path = folder / "analysis.f32"
        overlap_path = folder / "overlap.f32"
        output_path = folder / "output.laac"
        template_path.write_bytes(template)
        for source_path, target_path in zip(
                compressed, (analysis_path, overlap_path)):
            try:
                target_path.write_bytes(zlib.decompress(source_path.read_bytes()))
            except zlib.error as exc:
                raise ValueError(
                    "TAC encoder data is corrupt: %s" % source_path.name
                ) from exc
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        result = None
        for active_bands in TAC_BAND_LADDER:
            output_path.unlink(missing_ok=True)
            result = subprocess.run(
                [
                    str(encoder), str(template_path), str(source),
                    str(analysis_path), str(overlap_path), str(output_path),
                    str(capacity), "99", active_bands, str(sync_frames),
                    str(target_samples),
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=creationflags,
            )
            if not result.returncode and output_path.is_file():
                break
            report = result.stderr or result.stdout
            if not any(marker in report for marker in (
                    "exceeds logical template size",
                    "encoded stream needs",
            )):
                break
        if result.returncode or not output_path.is_file():
            detail = (result.stderr or result.stdout).strip().splitlines()
            message = detail[-1] if detail else "encoder produced no TAC stream"
            raise ValueError("TAC encoder could not encode %s: %s" % (
                source.name, message
            ))
        encoded = output_path.read_bytes()
    if progress and int(active_bands) <= TAC_REDUCED_BANDS:
        progress(
            "warning: %s fits only with %s active bands; its audio is "
            "noticeably softer than the other movies"
            % (source.name, active_bands)
        )
    info = tac_info(encoded)
    if info is None or info.samples != target_samples:
        raise ValueError("TAC encoder produced an invalid-duration stream")
    if len(encoded) > capacity:
        raise ValueError(
            "encoded TAC needs %d bytes but its packet slots hold %d"
            % (len(encoded), capacity)
        )
    return encoded


def decode_tac_to_wav(source, target, executable=None):
    """Decode one complete TAC stream; return ``False`` if no CLI exists."""
    source, target = Path(source), Path(target)
    decoder = Path(executable) if executable else find_vgmstream_cli()
    if decoder is None:
        return False
    target.unlink(missing_ok=True)
    creationflags = (
        subprocess.CREATE_NO_WINDOW
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )
    result = subprocess.run(
        [str(decoder), "-i", "-o", str(target), str(source)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=creationflags,
    )
    if result.returncode or not target.is_file():
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else "decoder produced no WAV"
        target.unlink(missing_ok=True)
        raise ValueError("vgmstream could not decode %s: %s" % (
            source.name, message
        ))
    read_pcm_wav(target)
    return True


def tac_info(data: bytes):
    """Return duration metadata for a complete tri-Ace TAC stream."""
    if not _looks_like_tac_header(data):
        return None
    frame_count, frame_last = struct.unpack_from("<HH", data, 0x0C)
    stream_size = struct.unpack_from("<I", data, 0x14)[0]
    if len(data) > stream_size or len(data) < stream_size - 0x4E000:
        return None
    return TacInfo((frame_count - 1) * 1024 + frame_last + 1, stream_size)

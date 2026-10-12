"""Explicit, time-preserving alignment for separately recorded microphones."""

from __future__ import annotations

import json
import math
from pathlib import Path

import audio_editing


def track_settings(assets, offsets: dict, gains: dict, names: dict) -> list[dict]:
    settings = []
    used = set()
    for index, asset in enumerate(assets, 1):
        aliases = [str(asset.source), asset.source.name, str(index)]

        def lookup(mapping, default):
            keys = [key for key in aliases if key in mapping]
            if len(keys) > 1:
                raise ValueError(f"Multiple settings identify track {index}: {keys}")
            if keys:
                used.add(keys[0])
                return mapping[keys[0]]
            return default

        offset = lookup(offsets, 0.0)
        gain = lookup(gains, 0.0)
        name = lookup(names, f"Microphone {index}")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (offset, gain)):
            raise ValueError("Track offsets/gains must be finite numbers.")
        if not 0 <= offset <= 86400 or not -36 <= gain <= 18:
            raise ValueError("Track offset must be 0..86400 seconds; gain must be -36..18 dB.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Track names must be non-empty strings.")
        settings.append(
            {
                "index": index,
                "source_file": str(asset.source),
                "cleaned_file": str(asset.cleaned_wav),
                "offset": offset,
                "gain_db": gain,
                "name": name,
            }
        )
    unknown = (set(offsets) | set(gains) | set(names)) - used
    if unknown:
        raise ValueError(f"Unknown track setting keys: {sorted(unknown)}")
    for mapping in (offsets, gains, names):
        for key in mapping:
            matches = [a for i, a in enumerate(assets, 1) if key in (str(a.source), a.source.name, str(i))]
            if len(matches) != 1:
                raise ValueError(f"Ambiguous track key {key!r}; use a one-based track index.")
    return settings


def mix_tracks(ffmpeg: str, settings: list[dict], output: Path, report: Path) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    filters, labels = [], []
    for index, row in enumerate(settings):
        command += ["-i", row["cleaned_file"]]
        # Sample-based delay avoids millisecond rounding on the shared 48 kHz clock.
        delay = round(row["offset"] * 48000)
        filters.append(f"[{index}:a]volume={row['gain_db']}dB,adelay={delay}S:all=1[a{index}]")
        labels.append(f"[a{index}]")
    filters.append("".join(labels) + f"amix=inputs={len(settings)}:duration=longest:normalize=0[mix]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[mix]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_f32le",
        str(output),
    ]
    audio_editing.execute(command)
    report.write_text(
        json.dumps(
            {
                "mode": "multitrack",
                "coordinate_system": "shared_mix_seconds",
                "automatic_acoustic_sync": False,
                "tracks": settings,
                "notes": [
                    "Offsets are explicit; no acoustic synchronization was inferred.",
                    "No automatic crosstalk removal or speaker isolation was applied.",
                    "Track names describe microphones, not verified speaker identities.",
                    "Independent preprocessing precedes gain and summation; float mix prevents clipping.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

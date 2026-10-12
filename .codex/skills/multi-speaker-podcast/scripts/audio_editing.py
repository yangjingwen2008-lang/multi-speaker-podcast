"""Source-time-preserving edit ranges, loudness measurement, and WAV cues."""

from __future__ import annotations

import json
import math
import re
import struct
import subprocess
from pathlib import Path
from typing import Any


def execute(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
    if proc.returncode:
        raise RuntimeError(proc.stderr[-6000:])
    return proc


def detect_silences(ffmpeg: str, source: Path, total: float, minimum: float = 2.5) -> list[tuple[float, float]]:
    proc = execute(
        [ffmpeg, "-hide_banner", "-i", str(source), "-af", f"silencedetect=noise=-50dB:d={minimum}", "-f", "null", "-"]
    )
    spans = []
    start = None
    for match in re.finditer(r"silence_(start|end):\s*([0-9.]+)", proc.stderr):
        kind, value = match.group(1), float(match.group(2))
        if kind == "start":
            start = value
        elif start is not None:
            spans.append((start, min(value, total)))
            start = None
    if start is not None:
        spans.append((start, total))
    return spans


def keep_ranges(total: float, silences: list[tuple[float, float]], padding: float = 0.5) -> list[tuple[float, float]]:
    """Cut only pause interiors; retain breathing room on either side."""
    removed = []
    for start, end in sorted(silences):
        lo, hi = max(0.0, start + padding), min(total, end - padding)
        if hi > lo:
            if removed and lo <= removed[-1][1]:
                removed[-1] = (removed[-1][0], max(hi, removed[-1][1]))
            else:
                removed.append((lo, hi))
    ranges, cursor = [], 0.0
    for lo, hi in removed:
        if lo > cursor:
            ranges.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < total:
        ranges.append((cursor, total))
    return ranges


def measure_loudness(ffmpeg: str, source: Path, target: float) -> dict[str, str]:
    proc = execute(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(source),
            "-af",
            f"loudnorm=I={target}:TP=-2:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    matches = re.findall(r"\{\s*\"input_i\".*?\}", proc.stderr, re.S)
    if not matches:
        raise RuntimeError("FFmpeg did not return loudness measurements.")
    return json.loads(matches[-1])


def normalize_master(ffmpeg: str, source: Path, master: Path, target: float) -> dict[str, str]:
    measured = measure_loudness(ffmpeg, source, target)
    fields = {
        "measured_I": "input_i",
        "measured_TP": "input_tp",
        "measured_LRA": "input_lra",
        "measured_thresh": "input_thresh",
        "offset": "target_offset",
    }
    finite = all(math.isfinite(float(measured[key])) for key in fields.values())
    if finite:
        filter_value = f"loudnorm=I={target}:TP=-2:LRA=11:linear=true:" + ":".join(
            f"{name}={measured[key]}" for name, key in fields.items()
        )
    else:
        filter_value = "anull"
    execute(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            filter_value,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(master),
        ]
    )
    return measured


def quality_report(ffmpeg: str, final: Path, master: Path, target: float, output: Path) -> dict[str, Any]:
    # Measure the encoded release, because lossy encoding can change true peak.
    measured = measure_loudness(ffmpeg, final, target)
    execute([ffmpeg, "-v", "error", "-i", str(final), "-f", "null", "-"])
    integrated, peak = float(measured["input_i"]), float(measured["input_tp"])
    qa = {
        "final_audio": str(final),
        "master": str(master),
        "decode_ok": True,
        "integrated_lufs": integrated if math.isfinite(integrated) else None,
        "true_peak_dbtp": peak if math.isfinite(peak) else None,
        "target_lufs": target,
        "loudness_within_1_lu": math.isfinite(integrated) and abs(integrated - target) <= 1,
        "peak_below_minus_1_dbtp": math.isfinite(peak) and peak <= -1,
        "subjective_listen_completed": False,
    }
    (output / "audio_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 音频质量检查",
        "",
        "- 完整解码：通过",
        f"- 成品响度：{integrated:.2f} LUFS（目标 {target}）",
        f"- 成品真峰值：{peak:.2f} dBTP",
        f"- 响度误差不超过 1 LU：{qa['loudness_within_1_lu']}",
        f"- 真峰值不超过 -1 dBTP：{qa['peak_below_minus_1_dbtp']}",
        "- 尚未进行人工全片听审；以上是技术检测，不代表内容和听感已获人工确认。",
    ]
    (output / "audio_qa_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return qa


def embed_wav_markers(wav: Path, markers: list[dict[str, Any]], sample_rate: int = 48000) -> bool:
    """Append standard RIFF cue/LIST chunks, which Audition can read as markers."""
    if wav.stat().st_size >= 0xFFFFFFFF - 65536:
        return False
    cues, labels = [], []
    for index, marker in enumerate(markers, 1):
        offset = round(float(marker["timeline_start"]) * sample_rate)
        cues.append(struct.pack("<II4sIII", index, offset, b"data", 0, 0, offset))
        label = struct.pack("<I", index) + str(marker["clip_id"]).encode("utf-8") + b"\0"
        labels.append(b"labl" + struct.pack("<I", len(label)) + label + (b"\0" if len(label) % 2 else b""))
    cue = struct.pack("<I", len(cues)) + b"".join(cues)
    adtl = b"adtl" + b"".join(labels)
    chunks = b"cue " + struct.pack("<I", len(cue)) + cue + b"LIST" + struct.pack("<I", len(adtl)) + adtl
    with wav.open("r+b") as fh:
        if fh.read(4) != b"RIFF":
            return False
        fh.seek(0, 2)
        fh.write(chunks)
        size = fh.tell()
        fh.seek(4)
        fh.write(struct.pack("<I", size - 8))
    return True

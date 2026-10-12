#!/usr/bin/env python3
"""MVP pipeline for turning multi-speaker interview audio into a podcast package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import audio_editing
import audition_bridge
import audition_session
import multitrack
import music_fetcher
import review_package
import run_state
import setup_ai_env

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


@dataclass
class AudioAsset:
    source: Path
    original_copy: Path
    working_wav: Path
    cleaned_wav: Path
    info: dict[str, Any]


@dataclass
class TranscriptSegment:
    source_file: str
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class EditSegment:
    clip_id: str
    source_file: str
    cleaned_file: str
    speaker: str
    start: float
    end: float
    duration: float
    text: str
    value_score: int
    density_score: int
    fluency_score: int
    keep: bool
    reason: str


def run(cmd: list[str], timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stderr or proc.stdout}")
    return proc


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Required tool not found: {name}")
    return path


def ffprobe_info(ffprobe: str, path: Path) -> dict[str, Any]:
    proc = run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(proc.stdout)


def duration_from_info(info: dict[str, Any]) -> float:
    fmt = info.get("format", {})
    try:
        return float(info.get("decoded_duration", fmt.get("duration")) or 0)
    except Exception:
        return 0.0


def duration(ffprobe: str, path: Path) -> float:
    return duration_from_info(ffprobe_info(ffprobe, path))


def seconds_to_ts(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def sanitize_filename(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", text)[:80] or "audio"


def normalize_to_wav(ffmpeg: str, source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        timeout=3600,
    )


def clean_audio(ffmpeg: str, source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = ",".join(
        [
            "highpass=f=80",
            "lowpass=f=12000",
            "afftdn=nr=8:nf=-45",
            "acompressor=threshold=-18dB:ratio=2.5:attack=20:release=250",
            "loudnorm=I=-23:TP=-2:LRA=11",
        ]
    )
    run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-af",
            filters,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        timeout=3600,
    )


def managed_python() -> Path:
    return setup_ai_env.venv_python(setup_ai_env.DEFAULT_ENV)


def python_has_module(py: Path, module: str) -> bool:
    try:
        proc = subprocess.run(
            [str(py), "-c", f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def choose_ai_python(args: argparse.Namespace, report_lines: list[str]) -> Path | None:
    if args.install_ai_deps:
        dep_report = Path(args.output_dir) / "dependency_report.md"
        setup_args = argparse.Namespace(
            env_dir=str(setup_ai_env.DEFAULT_ENV),
            compute=args.compute,
            report=str(dep_report),
            dry_run=False,
        )
        try:
            report = setup_ai_env.setup_env(setup_args)
            setup_ai_env.write_report(report, dep_report)
            report_lines.append(f"AI dependency setup success: {report.success}. See {dep_report}.")
        except Exception as exc:
            dep_report.write_text(f"# AI setup failed\n\n{exc}\n", encoding="utf-8")
            report_lines.append(f"AI setup failed; audio processing continues: {exc}")
    if args.ai_env in {"auto", "managed"}:
        py = managed_python()
        if py.exists():
            return py
        if args.ai_env == "managed":
            report_lines.append(f"Managed AI Python does not exist: {py}")
            return None
    if args.ai_env in {"auto", "current"}:
        return Path(sys.executable)
    return None


def transcribe_with_faster_whisper(
    py: Path,
    audio: Path,
    model_name: str,
    compute: str,
    output_json: Path,
) -> tuple[bool, str]:
    if not python_has_module(py, "faster_whisper"):
        return False, f"faster_whisper is not installed in {py}."
    device = compute
    code = r"""
import json, sys, os
from pathlib import Path
torch_lib = Path(sys.prefix) / "Lib/site-packages/torch/lib"
if os.name == "nt" and torch_lib.exists():
    os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
    dll_directory = os.add_dll_directory(str(torch_lib))
from faster_whisper import WhisperModel
audio, model_name, device, out = sys.argv[1:5]
if device == "auto":
    import ctranslate2
    device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
compute_type = "float16" if device == "cuda" else "int8"
def transcribe(device):
    model = WhisperModel(model_name, device=device, compute_type="float16" if device == "cuda" else "int8")
    segments, info = model.transcribe(audio, vad_filter=True, beam_size=5, word_timestamps=True)
    rows = [{"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(),
             "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (seg.words or [])]} for seg in segments]
    return rows, info
try:
    rows, info = transcribe(device)
except Exception:
    if device == "cpu":
        raise
    print("CUDA transcription failed; retrying on CPU.", file=sys.stderr)
    rows, info = transcribe("cpu")
    device = "cpu"
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"language": info.language, "device": device, "segments": rows}, fh, ensure_ascii=False, indent=2)
"""
    try:
        proc = subprocess.run(
            [str(py), "-c", code, str(audio), model_name, device, str(output_json)],
            capture_output=True,
            text=True,
            timeout=7200,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        if compute != "cpu":
            ok, message = transcribe_with_faster_whisper(py, audio, model_name, "cpu", output_json)
            return ok, f"GPU/auto subprocess failed; CPU retry: {message}"
        return False, proc.stderr or proc.stdout
    data = json.loads(output_json.read_text(encoding="utf-8"))
    return True, f"transcribed on {data.get('device', 'unknown')}"


def diarize_with_pyannote(
    py: Path, audio: Path, token: str | None, output_json: Path, compute: str = "auto"
) -> tuple[bool, str]:
    if not token:
        return False, "No Hugging Face token was provided; pyannote diarization was skipped."
    if not python_has_module(py, "pyannote.audio"):
        return False, f"pyannote.audio is not installed in {py}."
    code = r"""
import json, sys, os, torch, wave
from pyannote.audio import Pipeline
audio, out, compute = sys.argv[1:4]
pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=os.environ["HUGGINGFACE_TOKEN"])
if compute != "cpu" and torch.cuda.is_available():
    pipeline.to(torch.device("cuda"))
# Cleaned WAV is PCM16. Loading it directly avoids a Windows shared-FFmpeg decoder requirement.
with wave.open(audio, "rb") as wav:
    rate, channels = wav.getframerate(), wav.getnchannels()
    if wav.getsampwidth() != 2:
        raise ValueError("Diarization input must be PCM16 WAV.")
    waveform = torch.frombuffer(bytearray(wav.readframes(wav.getnframes())), dtype=torch.int16)
waveform = waveform.reshape(-1, channels).T.float().mean(dim=0, keepdim=True) / 32768.0
payload = {"waveform": waveform, "sample_rate": rate}
try:
    output = pipeline(payload)
except RuntimeError:
    if compute == "cpu" or not torch.cuda.is_available():
        raise
    print("GPU diarization failed; retrying on CPU.", file=sys.stderr)
    pipeline.to(torch.device("cpu"))
    output = pipeline(payload)
diarization = getattr(output, "exclusive_speaker_diarization", None)
if diarization is None:
    diarization = getattr(output, "speaker_diarization", output)
rows = []
for turn, _, speaker in diarization.itertracks(yield_label=True):
    rows.append({"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)})
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"segments": rows}, fh, ensure_ascii=False, indent=2)
"""
    env = os.environ.copy()
    env["HUGGINGFACE_TOKEN"] = token
    try:
        proc = subprocess.run(
            [str(py), "-c", code, str(audio), str(output_json), compute],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=7200,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc).replace(token, "[REDACTED]")
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).replace(token, "[REDACTED]")
    return True, "diarized"


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def speaker_for_segment(segment: dict[str, Any], diarization_rows: list[dict[str, Any]]) -> str:
    best = ("Unknown", 0.0)
    for row in diarization_rows:
        value = overlap(float(segment["start"]), float(segment["end"]), float(row["start"]), float(row["end"]))
        if value > best[1]:
            best = (str(row.get("speaker") or "Speaker 1"), value)
    return best[0]


def transcript_segments(
    assets: list[AudioAsset],
    ai_python: Path | None,
    args: argparse.Namespace,
    reports_dir: Path,
    report_lines: list[str],
) -> list[TranscriptSegment]:
    all_segments: list[TranscriptSegment] = []
    if not ai_python or args.skip_ai:
        report_lines.append(
            "AI skipped by request." if args.skip_ai else "No AI Python environment; transcription skipped."
        )
        return all_segments

    for asset in assets:
        stem = asset.cleaned_wav.stem
        trans_json = reports_dir / f"{stem}_transcript.json"
        diar_json = reports_dir / f"{stem}_diarization.json"
        ok, msg = transcribe_with_faster_whisper(
            ai_python, asset.cleaned_wav, args.whisper_model, args.compute, trans_json
        )
        report_lines.append(f"Transcription for {asset.source.name}: {msg}")
        if not ok:
            continue
        diar_rows: list[dict[str, Any]] = []
        ok_d, msg_d = diarize_with_pyannote(
            ai_python, asset.cleaned_wav, args.hf_token or os.environ.get("HUGGINGFACE_TOKEN"), diar_json, args.compute
        )
        report_lines.append(f"Diarization for {asset.source.name}: {msg_d}")
        if ok_d:
            diar_rows = json.loads(diar_json.read_text(encoding="utf-8")).get("segments", [])
        data = json.loads(trans_json.read_text(encoding="utf-8"))
        labels = {name: f"Speaker {index}" for index, name in enumerate(sorted({r["speaker"] for r in diar_rows}), 1)}
        for row in data.get("segments", []):
            label = speaker_for_segment(row, diar_rows) if diar_rows else "Unknown"
            speaker = labels.get(label, label)
            speaker = args.speaker_map.get(f"{asset.source.name}:{speaker}", args.speaker_map.get(speaker, speaker))
            all_segments.append(
                TranscriptSegment(
                    source_file=str(asset.source),
                    start=float(row["start"]),
                    end=float(row["end"]),
                    speaker=speaker,
                    text=str(row.get("text") or "").strip(),
                )
            )
    return all_segments


def clean_text(text: str) -> str:
    # Remove only isolated filler interjections, not meaningful Chinese connectors.
    text = re.sub(r"(^|[，,。.!！?？;；\s])(?:嗯+|呃+|额+)(?=[，,。.!！?？;；\s]|$)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lstrip("，,;； ")


def score_segment(text: str, dur: float) -> tuple[int, int, int, bool, str]:
    cleaned = clean_text(text)
    words = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", cleaned)
    filler_count = len(re.findall(r"(嗯|啊|呃|就是|然后|那个|这个)", text))
    if not text:
        return 3, 3, 5, True, "No transcript; preserve audio for review."
    density = min(10, max(1, int(len(words) / max(dur / 12, 1))))
    value = min(
        10, max(1, 4 + density // 2 + (2 if re.search(r"因为|所以|关键|重要|经验|故事|问题|建议|结论", text) else 0))
    )
    fluency = max(1, min(10, 9 - min(filler_count, 5)))
    keep = bool(re.sub(r"[\W_]+", "", cleaned))
    reason = (
        "Preserve speech; heuristic scores are review cues only."
        if keep
        else "Isolated filler candidate; listen in context before deleting."
    )
    return value, density, fluency, keep, reason


def build_edit_segments(assets: list[AudioAsset], segments: list[TranscriptSegment]) -> list[EditSegment]:
    cleaned_by_source = {str(asset.source): str(asset.cleaned_wav) for asset in assets}
    edits: list[EditSegment] = []
    for index, segment in enumerate(segments, start=1):
        dur = max(segment.end - segment.start, 0.0)
        value, density, fluency, keep, reason = score_segment(segment.text, dur)
        edits.append(
            EditSegment(
                clip_id=f"clip_{index:04d}",
                source_file=segment.source_file,
                cleaned_file=cleaned_by_source.get(segment.source_file, segment.source_file),
                speaker=segment.speaker,
                start=segment.start,
                end=segment.end,
                duration=dur,
                text=segment.text,
                value_score=value,
                density_score=density,
                fluency_score=fluency,
                keep=True,
                reason=reason,
            )
        )
    return edits


def cut_clip(ffmpeg: str, edit: EditSegment, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fade = min(0.018, max(edit.duration / 8, 0.0))
    if edit.duration > fade * 2 and fade > 0:
        afade = f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={max(edit.duration - fade, 0):.3f}:d={fade:.3f}"
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{edit.start:.3f}",
            "-t",
            f"{edit.duration:.3f}",
            "-i",
            edit.cleaned_file,
            "-af",
            afade,
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output),
        ]
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{edit.start:.3f}",
            "-t",
            f"{edit.duration:.3f}",
            "-i",
            edit.cleaned_file,
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output),
        ]
    run(cmd, timeout=3600)


def concat_audio(ffmpeg: str, files: list[Path], output: Path, target_lufs: float, target_format: str) -> None:
    if not files:
        raise ValueError("No clips selected. Select at least one non-empty speech clip.")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.parent / "concat_list.txt"
    concat_lines = []
    for path in files:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        concat_lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    merged = output.parent / "work" / "assembled.wav"
    run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(merged),
        ],
        timeout=3600,
    )
    master = output.parent / "master_podcast.wav"
    audio_editing.normalize_master(ffmpeg, merged, master, target_lufs)
    if target_format == "mp3":
        run(
            [ffmpeg, "-y", "-v", "error", "-i", str(master), "-c:a", "libmp3lame", "-b:a", "192k", str(output)],
            timeout=3600,
        )
    else:
        shutil.copy2(master, output)


def write_markdown_outputs(
    output_dir: Path,
    args: argparse.Namespace,
    assets: list[AudioAsset],
    edits: list[EditSegment],
    report_lines: list[str],
    analysis: list[EditSegment] | None = None,
    music: dict[str, Any] | None = None,
    intro_duration: float = 0,
) -> None:
    report_lines = [line.replace(args.hf_token, "[REDACTED]") if args.hf_token else line for line in report_lines]
    report_lines = [
        line.replace(args.jamendo_client_id, "[REDACTED]") if args.jamendo_client_id else line for line in report_lines
    ]
    raw_lines = ["# Raw Transcript", ""]
    cleaned_lines = ["# Cleaned Transcript", ""]
    for edit in analysis if analysis is not None else edits:
        raw_lines.append(
            f"## {Path(edit.source_file).name} / {edit.speaker} {seconds_to_ts(edit.start)} - {seconds_to_ts(edit.end)}"
        )
        raw_lines.append(edit.text or "[No transcript text available in this MVP run.]")
        raw_lines.append("")
    timeline_cursor = intro_duration
    for edit in edits:
        for item in analysis if analysis is not None else edits:
            if item.source_file == edit.source_file and overlap(item.start, item.end, edit.start, edit.end) > 0:
                start = timeline_cursor + max(item.start, edit.start) - edit.start
                cleaned_lines.append(f"## {item.speaker} {seconds_to_ts(start)} / {Path(item.source_file).name}")
                cleaned_lines.append(clean_text(item.text) or "[转录缺失：保留音频，等待复听。]")
                if item.start < edit.start - 0.05 or item.end > edit.end + 0.05:
                    cleaned_lines.append("[原句跨越剪点：以上文字是原句参考，需复听确认成品片段。]")
                cleaned_lines.append("")
        timeline_cursor += edit.duration
    (output_dir / "transcript_raw.md").write_text("\n".join(raw_lines), encoding="utf-8")
    (output_dir / "transcript_cleaned.md").write_text("\n".join(cleaned_lines), encoding="utf-8")

    edl_lines = [
        "# Edit Decision List",
        "",
        "| clip_id | decision | source | time | speaker | value | density | fluency | reason |",
        "|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for edit in edits:
        decision = "KEEP" if edit.keep else "DELETE"
        edl_lines.append(
            f"| {edit.clip_id} | {decision} | {Path(edit.source_file).name} | "
            f"{seconds_to_ts(edit.start)}-{seconds_to_ts(edit.end)} | {edit.speaker} | "
            f"{edit.value_score} | {edit.density_score} | {edit.fluency_score} | {edit.reason} |"
        )
    edl_lines.extend(
        [
            "",
            "时间均为原始素材坐标；预处理 WAV 保持同一坐标。JSON 中 clips 顺序即成品顺序。",
            "未采用剪辑方案时，只压缩检测到的长静音，不按启发式评分删除谈话。",
            "完整保留/删除区间及原因见 edit_decision_list.json；分段评分见 content_scores.json。",
        ]
    )
    (output_dir / "edit_decision_list.md").write_text("\n".join(edl_lines) + "\n", encoding="utf-8")

    kept = [item for item in edits if item.keep]
    structure_lines = [
        "# Podcast Structure",
        "",
        f"- Podcast: {args.podcast_name or 'Untitled podcast'}",
        f"- Topic: {args.episode_topic or 'Untitled episode'}",
        f"- Host: {args.host or 'Not specified'}",
        f"- Guests: {', '.join(args.guest) if args.guest else 'Not specified'}",
        "",
        "## Suggested Flow",
        "- Opening hook: candidates below are suggestions; no unapproved semantic reorder was applied.",
        "- Formal intro: add or record a short intro naming the topic and guests if not present.",
        "- Main discussion: follow kept clips in the generated timeline order.",
        "- Summary: preserve any conclusion-like segment, or record a concise wrap-up.",
        "- Outro: use the generated outro music if the license report marks it safe.",
        "",
        "## Highlight Candidates",
    ]
    for edit in sorted(analysis or kept, key=lambda item: item.value_score, reverse=True)[:5]:
        structure_lines.append(
            f"- {edit.clip_id}: {seconds_to_ts(edit.start)} {clean_text(edit.text)[:160] or edit.reason}"
        )
    (output_dir / "podcast_structure.md").write_text("\n".join(structure_lines) + "\n", encoding="utf-8")

    show_lines = [
        "# Show Notes",
        "",
        "## Title",
        args.episode_topic or args.podcast_name or "Untitled interview episode",
        "",
        "## Description",
        f"本期围绕“{args.episode_topic or '本期主题'}”展开多人访谈讨论。",
        "",
        "## Timeline",
    ]
    running = intro_duration
    if intro_duration:
        show_lines.append("- 00:00:00.000 片头音乐")
    for edit in kept:
        show_lines.append(f"- {seconds_to_ts(running)} {clean_text(edit.text)[:80] or edit.clip_id}")
        running += edit.duration
    show_lines.extend(["", "## Tags", "#播客 #访谈 #对话"])
    if music and music.get("auto_mix_ready"):
        show_lines.extend(["", "## 音乐署名"])
        for track in music.get("selected", []):
            show_lines.append(
                f"- {track.get('title', 'Music')} / {track.get('creator') or 'Unknown'}; "
                f"{track.get('license_url') or track.get('license')}; {track.get('landing_url') or ''}; "
                "用于片头片尾，已截短、淡入淡出及调整响度。"
            )
    (output_dir / "show_notes.md").write_text("\n".join(show_lines) + "\n", encoding="utf-8")

    info_lines = ["# Audio Info Report", ""]
    for asset in assets:
        stream = next((s for s in asset.info.get("streams", []) if s.get("codec_type") == "audio"), {})
        info_lines.extend(
            [
                f"## {asset.source.name}",
                f"- Duration: {duration_from_info(asset.info):.2f}s",
                f"- Codec: {stream.get('codec_name', 'unknown')}",
                f"- Sample rate: {stream.get('sample_rate', 'unknown')}",
                f"- Channels: {stream.get('channels', 'unknown')}",
                f"- Bit rate: {asset.info.get('format', {}).get('bit_rate', 'unknown')}",
                "",
            ]
        )
    (output_dir / "audio_info_report.md").write_text("\n".join(info_lines), encoding="utf-8")

    report = [
        "# Edit Report",
        "",
        "## Processing",
        "- Converted input audio to 48 kHz stereo WAV.",
        "- Time-preserving cleanup: high-pass, low-pass, light FFT denoise, compression, and loudness normalization.",
        "- Long pauses are shortened only through explicit source-time edit ranges, with 0.5s padding.",
        "- Exported 48kHz/24-bit WAV master; final loudness uses two-pass measurement. See audio_qa_report.md.",
        "- Semantic scoring is heuristic; it does not detect all off-topic/repetitive content or guarantee editorial quality.",
        f"- Target loudness: {args.target_lufs} LUFS.",
        f"- Target format: {args.target_format}.",
        "",
        "## Runtime Notes",
        *[f"- {line}" for line in report_lines],
    ]
    (output_dir / "edit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def write_timeline_csv(path: Path, edits: list[EditSegment], offset: float = 0) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "clip_id",
                "source_file",
                "speaker",
                "start_time",
                "end_time",
                "duration",
                "content_summary",
                "decision",
                "reason",
                "cleaned_file",
                "timeline_start",
                "timeline_end",
                "clip_file",
            ],
        )
        writer.writeheader()
        for edit in edits:
            writer.writerow(
                {
                    "clip_id": edit.clip_id,
                    "source_file": edit.source_file,
                    "speaker": edit.speaker,
                    "start_time": seconds_to_ts(edit.start),
                    "end_time": seconds_to_ts(edit.end),
                    "duration": f"{edit.duration:.3f}",
                    "content_summary": clean_text(edit.text)[:180] or edit.reason,
                    "decision": "keep" if edit.keep else "delete",
                    "reason": edit.reason,
                    "cleaned_file": edit.cleaned_file,
                    "timeline_start": seconds_to_ts(offset),
                    "timeline_end": seconds_to_ts(offset + edit.duration),
                    "clip_file": f"clips/{edit.clip_id}.wav",
                }
            )
            offset += edit.duration


def copy_inputs(inputs: list[Path], output_dir: Path, ffprobe: str, ffmpeg: str) -> list[AudioAsset]:
    original_dir = output_dir / "audition_package" / "original_audio"
    work_dir = output_dir / "work"
    cleaned_dir = output_dir / "audition_package" / "cleaned_audio"
    assets: list[AudioAsset] = []
    for index, source in enumerate(inputs, start=1):
        if not source.exists():
            raise SystemExit(f"Input audio file does not exist: {source}")
        if source.suffix.lower() not in AUDIO_EXTS:
            raise SystemExit(f"Unsupported audio extension: {source}")
        base = f"{index:02d}_{sanitize_filename(source.stem)}"
        original = original_dir / f"{index:02d}_{source.name}"
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, original)
        working = work_dir / f"{base}.wav"
        cleaned = cleaned_dir / f"{base}_cleaned.wav"
        info = ffprobe_info(ffprobe, source)
        if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])) or duration_from_info(info) <= 0:
            raise ValueError(f"No decodable non-empty audio stream: {source}")
        normalize_to_wav(ffmpeg, source, working)
        info["decoded_duration"] = duration(ffprobe, working)
        clean_audio(ffmpeg, working, cleaned)
        if abs(duration(ffprobe, cleaned) - duration(ffprobe, working)) > 0.1:
            raise ValueError(f"Preprocessing changed source time coordinates: {source}")
        assets.append(AudioAsset(source.resolve(), original.resolve(), working.resolve(), cleaned.resolve(), info))
    return assets


def resolve_source(value: str, assets: list[AudioAsset], base: Path) -> AudioAsset:
    candidate = (base / value).resolve()
    matches = [a for a in assets if a.source == candidate]
    if not matches:
        matches = [a for a in assets if a.source.name == value]
    if not matches and Path(value).name == "multitrack_mix.wav":
        matches = [a for a in assets if a.source.name == "multitrack_mix.wav"]
    if len(matches) != 1:
        raise ValueError(f"Source must identify exactly one input: {value}")
    return matches[0]


def validated_range(row: dict[str, Any], asset: AudioAsset) -> tuple[float, float]:
    if not isinstance(row, dict) or any(
        isinstance(row.get(k), bool) or not isinstance(row.get(k), (int, float)) for k in ("start", "end")
    ):
        raise ValueError("Audio ranges require numeric start/end fields, not booleans or strings.")
    start, end = float(row["start"]), float(row["end"])
    total = duration_from_info(asset.info)
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < min(end, total) or end > total + 0.05:
        raise ValueError(f"Invalid range {start}..{end} for {asset.source.name}")
    return start, min(end, duration_from_info(asset.info))


def load_transcript(path: Path, assets: list[AudioAsset], speaker_map: dict[str, str]) -> list[TranscriptSegment]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Transcript JSON must be an object.")
    if data.get("coordinate_system", "source_seconds") != "source_seconds":
        raise ValueError("Transcript coordinates must be source_seconds, not final timeline seconds.")
    rows = data.get("segments", [])
    if not isinstance(rows, list):
        raise ValueError("Transcript JSON requires a segments array.")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_file"), str):
            raise ValueError("Transcript segments require a source_file string.")
        if not isinstance(row.get("text", ""), str) or not isinstance(row.get("speaker", "Unknown"), str):
            raise ValueError("Transcript text and speaker must be strings.")
        asset = resolve_source(row["source_file"], assets, path.parent)
        start, end = validated_range(row, asset)
        label = str(row.get("speaker", "Unknown"))
        label = speaker_map.get(f"{asset.source.name}:{label}", speaker_map.get(label, label))
        result.append(TranscriptSegment(str(asset.source), start, end, label, str(row.get("text", ""))))
    return result


def build_render_plan(
    assets: list[AudioAsset], analysis: list[EditSegment], args: argparse.Namespace, ffmpeg: str
) -> list[EditSegment]:
    rows = []
    if args.edit_plan:
        plan_path = Path(args.edit_plan)
        data = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Edit plan JSON must be an object.")
        if data.get("coordinate_system", "source_seconds") != "source_seconds":
            raise ValueError("Edit plan coordinates must be source_seconds.")
        if not isinstance(data.get("clips"), list) or not data["clips"]:
            raise ValueError("Edit plan requires at least one clip in clips.")
        for row in data["clips"]:
            if not isinstance(row, dict) or not isinstance(row.get("source_file"), str):
                raise ValueError("Edit clips require a source_file string.")
            if "keep" in row and not isinstance(row["keep"], bool):
                raise ValueError("Clip keep must be a boolean.")
            if row.get("keep") is False:
                continue
            asset = resolve_source(row["source_file"], assets, plan_path.parent)
            start, end = validated_range(row, asset)
            rows.append((asset, start, end, str(row.get("reason", "User-supplied edit plan"))))
        if not rows:
            raise ValueError("Edit plan does not retain any clips.")
    else:
        for asset in assets:
            total = duration_from_info(asset.info)
            silences = (
                audio_editing.detect_silences(ffmpeg, asset.working_wav, total, args.silence_min)
                if args.trim_silence
                else []
            )
            for start, end in audio_editing.keep_ranges(total, silences):
                rows.append((asset, start, end, "Preserve conversation; only shorten measured long silence."))
    edits = []
    for index, (asset, start, end, reason) in enumerate(rows, 1):
        covered = [
            r for r in analysis if r.source_file == str(asset.source) and overlap(r.start, r.end, start, end) > 0
        ]
        text = " ".join(r.text for r in covered if r.text)
        speakers = sorted({r.speaker for r in covered})
        value, density, fluency, _, _ = score_segment(text, end - start)
        edits.append(
            EditSegment(
                f"clip_{index:04d}",
                str(asset.source),
                str(asset.cleaned_wav),
                ", ".join(speakers) or "Unknown",
                start,
                end,
                end - start,
                text,
                value,
                density,
                fluency,
                True,
                reason,
            )
        )
    return edits


def plan_document(assets: list[AudioAsset], edits: list[EditSegment], intro_duration: float) -> dict[str, Any]:
    clips, deleted = [], []
    cursor = intro_duration
    for edit in edits:
        row = asdict(edit)
        row.update(timeline_start=cursor, timeline_end=cursor + edit.duration)
        clips.append(row)
        cursor += edit.duration
    for asset in assets:
        spans = sorted((e.start, e.end) for e in edits if e.source_file == str(asset.source))
        position = 0.0
        for start, end in spans:
            if start > position + 0.001:
                deleted.append(
                    {
                        "source_file": str(asset.source),
                        "start": position,
                        "end": start,
                        "reason": "Omitted by explicit render plan",
                    }
                )
            position = max(position, end)
        total = duration_from_info(asset.info)
        if position < total - 0.001:
            deleted.append(
                {
                    "source_file": str(asset.source),
                    "start": position,
                    "end": total,
                    "reason": "Omitted by explicit render plan",
                }
            )
    return {
        "coordinate_system": "source_seconds",
        "clips": clips,
        "deleted_ranges": deleted,
        "speech_timeline_end": cursor,
        "intro_duration": intro_duration,
    }


def prepare_audio(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    inputs = [Path(item).resolve() for item in args.input]
    for path in inputs:
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            raise ValueError(f"Missing or unsupported input audio: {path}")
        if path.is_relative_to(output_dir):
            raise ValueError("Inputs must be outside the output directory.")
    if len(set(inputs)) != len(inputs):
        raise ValueError("Duplicate input paths are not supported; duplicate desired clips in an edit plan instead.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with run_state.OutputLock(output_dir):
        if any(p.name != ".pipeline.lock" for p in output_dir.iterdir()) and not args.overwrite:
            raise ValueError("Output directory is not empty; choose a new directory or pass --overwrite.")
        run_id = run_state.begin(output_dir, args.overwrite)
        try:
            result = produce_audio(args, output_dir, inputs)
            run_state.finish(output_dir, run_id, result)
            return result
        except BaseException as exc:
            message = str(exc) or type(exc).__name__
            for secret in (args.hf_token, args.jamendo_client_id):
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            run_state.fail(output_dir, run_id, message)
            raise


def produce_audio(args: argparse.Namespace, output_dir: Path, inputs: list[Path]) -> int:
    reports_dir = output_dir / "work" / "analysis"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_tool("ffmpeg")
    ffprobe = require_tool("ffprobe")
    report_lines: list[str] = [f"ffmpeg: {ffmpeg}", f"ffprobe: {ffprobe}"]
    print("[1/6] Inspecting and cleaning audio (source timestamps preserved)", flush=True)
    original_assets = copy_inputs(inputs, output_dir, ffprobe, ffmpeg)
    (output_dir / "source_audio_info.json").write_text(
        json.dumps(
            [{"source_file": str(asset.source), "info": asset.info} for asset in original_assets],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assets = original_assets
    track_rows = []
    if args.input_mode == "multitrack":
        track_rows = multitrack.track_settings(original_assets, args.track_offsets, args.track_gains, args.track_names)
        floating_mix = output_dir / "work" / "multitrack_float.wav"
        multitrack.mix_tracks(ffmpeg, track_rows, floating_mix, output_dir / "multitrack_report.json")
        mix = output_dir / "audition_package" / "cleaned_audio" / "multitrack_mix.wav"
        audio_editing.normalize_master(ffmpeg, floating_mix, mix, -23)
        # The diarization WAV reader requires standard PCM16, not extensible/float WAV.
        analysis_mix = output_dir / "work" / "multitrack_analysis.wav"
        normalize_to_wav(ffmpeg, mix, analysis_mix)
        info = ffprobe_info(ffprobe, mix)
        info["decoded_duration"] = duration(ffprobe, mix)
        expected_duration = max(
            round(row["offset"] * 48000) / 48000 + duration_from_info(asset.info)
            for row, asset in zip(track_rows, original_assets)
        )
        if abs(info["decoded_duration"] - expected_duration) > 0.05:
            raise ValueError("Multitrack mix changed the shared source timeline duration.")
        assets = [AudioAsset(mix, mix, analysis_mix, mix, info)]
        report_lines.append(
            "Multitrack: explicit offsets, per-track cleanup and gains; shared mix timestamps. No automatic acoustic sync or crosstalk removal."
        )

    identity = hashlib.sha256()
    for asset in original_assets:
        with asset.source.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                identity.update(block)
        identity.update(b"\x00INPUT_BOUNDARY\x00")
    identity.update(
        json.dumps(
            {
                "mode": args.input_mode,
                "tracks": [{key: row[key] for key in ("offset", "gain_db")} for row in track_rows],
            },
            sort_keys=True,
        ).encode()
    )
    source_identity = identity.hexdigest()
    for value in (args.transcript_json, args.edit_plan):
        if value:
            reused = json.loads(Path(value).read_text(encoding="utf-8-sig"))
            if not isinstance(reused, dict):
                raise ValueError("Reused transcript/edit plan must be a JSON object.")
            if reused.get("source_identity") and reused["source_identity"] != source_identity:
                raise ValueError(
                    "Reused transcript/edit plan belongs to different audio, input order or microphone alignment."
                )

    print("[2/6] Transcription and speaker analysis", flush=True)
    if args.transcript_json:
        segments = load_transcript(Path(args.transcript_json), assets, args.speaker_map)
    else:
        ai_python = None if args.skip_ai else choose_ai_python(args, report_lines)
        segments = transcript_segments(assets, ai_python, args, reports_dir, report_lines)
    for asset in assets:
        if not any(s.source_file == str(asset.source) for s in segments):
            report_lines.append(f"No transcript for {asset.source.name}; preserving full audio.")
            segments.append(TranscriptSegment(str(asset.source), 0, duration_from_info(asset.info), "Unknown", ""))
    (output_dir / "transcript.json").write_text(
        json.dumps(
            {
                "coordinate_system": "source_seconds",
                "input_mode": args.input_mode,
                "source_identity": source_identity,
                "segments": [
                    dict(asdict(s), source_file=Path(s.source_file).name)
                    if sum(a.source.name == Path(s.source_file).name for a in assets) == 1
                    else asdict(s)
                    for s in segments
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    analysis = build_edit_segments(assets, segments)
    scores = []
    for item in analysis:
        row = asdict(item)
        row["suggested_keep"] = score_segment(item.text, item.duration)[3]
        row["review_required"] = True
        row["chitchat_candidate"] = (
            bool(re.search(r"你好|最近怎么样|天气|hello|how are you", item.text, re.I)) and not args.keep_chitchat
        )
        scores.append(row)
    (output_dir / "content_scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[3/6] Applying source-time edit ranges", flush=True)
    edits = build_render_plan(assets, analysis, args, ffmpeg)
    clips_dir = output_dir / "audition_package" / "clips"
    clip_files: list[Path] = []
    for edit in edits:
        if not edit.keep or edit.duration <= 0:
            continue
        clip_file = clips_dir / f"{edit.clip_id}.wav"
        cut_clip(ffmpeg, edit, clip_file)
        actual = duration(ffprobe, clip_file)
        if actual <= 0 or abs(actual - edit.duration) > 0.05:
            raise ValueError(f"Clip duration mismatch: {edit.clip_id} requested {edit.duration}, decoded {actual}")
        edit.duration = actual
        edit.end = edit.start + actual
        clip_files.append(clip_file)

    music_manifest: dict[str, Any] | None = None
    intro: Path | None = None
    outro: Path | None = None
    print("[4/6] Preparing music and license records", flush=True)
    if args.music_mode == "auto":
        music_manifest = music_fetcher.fetch_music(
            output_dir,
            args.podcast_name,
            args.episode_topic,
            args.music_style,
            args.music_provider,
            args.jamendo_client_id or os.environ.get("JAMENDO_CLIENT_ID"),
            ffmpeg,
            args.allow_cc_attribution_music,
        )
        if music_manifest.get("auto_mix_ready"):
            intro_value = music_manifest.get("intro_music")
            outro_value = music_manifest.get("outro_music")
            intro = Path(intro_value) if intro_value else None
            outro = Path(outro_value) if outro_value else None
        report_lines.append(
            f"Music fetch auto_mix_ready: {bool(music_manifest and music_manifest.get('auto_mix_ready'))}"
        )
    elif args.music_mode == "provided":
        music_manifest = music_fetcher.provided_music(
            output_dir,
            args.intro_music or args.music_file,
            args.outro_music or args.music_file,
            ffmpeg,
            args.music_license,
            args.music_creator,
        )
        intro = Path(music_manifest["intro_music"]) if music_manifest.get("intro_music") else None
        outro = Path(music_manifest["outro_music"]) if music_manifest.get("outro_music") else None
    else:
        (output_dir / "music_manifest.json").write_text(json.dumps({"music_mode": "off"}, indent=2), encoding="utf-8")
        (output_dir / "music_license_report.md").write_text(
            "# Music License Report\n\n- Music mode was off.\n", encoding="utf-8"
        )

    final_files: list[Path] = []
    if intro and intro.exists():
        final_files.append(intro)
    final_files.extend(clip_files)
    if outro and outro.exists():
        final_files.append(outro)
    final_name = f"final_podcast.{args.target_format}"
    final_path = output_dir / final_name
    print("[5/6] Rendering WAV master, final release, and loudness QA", flush=True)
    concat_audio(ffmpeg, final_files, final_path, args.target_lufs, args.target_format)
    intro_duration = duration(ffprobe, intro) if intro else 0
    plan = plan_document(assets, edits, intro_duration)
    plan.update(input_mode=args.input_mode, source_identity=source_identity)
    (output_dir / "edit_decision_list.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    master = output_dir / "master_podcast.wav"
    markers = plan["clips"].copy()
    if intro:
        markers.insert(0, {"clip_id": "intro_music", "timeline_start": 0, "duration": intro_duration})
    if outro:
        markers.append(
            {
                "clip_id": "outro_music",
                "timeline_start": plan["speech_timeline_end"],
                "duration": duration(ffprobe, outro),
            }
        )
    embedded = audio_editing.embed_wav_markers(master, markers)
    if args.target_format == "wav":
        shutil.copy2(master, final_path)
    report_lines.append(
        f"WAV cue markers embedded: {embedded}. Speaker labels are local to each file unless manually mapped."
    )
    if args.generate_intro_outro:
        report_lines.append("Intro/outro copy saved as intro_outro_script.md; no synthetic voice added.")
    qa = audio_editing.quality_report(ffmpeg, final_path, master, args.target_lufs, output_dir)
    technical_pass = all(qa[key] for key in ("decode_ok", "loudness_within_1_lu", "peak_below_minus_1_dbtp"))

    package_dir = output_dir / "audition_package"
    timeline_rows = [asdict(item) for item in edits]
    print("[6/6] Writing reports and Adobe Audition package", flush=True)
    write_timeline_csv(output_dir / "audition_timeline.csv", edits, intro_duration)
    write_timeline_csv(package_dir / "audition_timeline.csv", edits, intro_duration)
    audition_bridge.write_markers_csv(package_dir / "audition_markers.csv", markers)
    session_tracks = [
        {
            "name": "Interview",
            "clips": [
                {"file": file, "name": row["clip_id"], "start": row["timeline_start"], "duration": row["duration"]}
                for file, row in zip(clip_files, plan["clips"])
            ],
        }
    ]
    music_clips = []
    if intro:
        music_clips.append({"file": intro, "name": "Intro music", "start": 0, "duration": intro_duration})
    if outro:
        music_clips.append(
            {
                "file": outro,
                "name": "Outro music",
                "start": plan["speech_timeline_end"],
                "duration": duration(ffprobe, outro),
            }
        )
    if music_clips:
        session_tracks.append({"name": "Music", "clips": music_clips})
    sessions = [audition_session.write_session(package_dir / "podcast_edit.sesx", session_tracks)]
    if track_rows:
        source_tracks = [
            {
                "name": row["name"],
                "gain_db": row["gain_db"],
                "clips": [
                    {
                        "file": asset.cleaned_wav,
                        "name": asset.source.name,
                        "start": row["offset"],
                        "duration": duration_from_info(asset.info),
                    }
                ],
            }
            for row, asset in zip(track_rows, original_assets)
        ]
        sessions.append(audition_session.write_session(package_dir / "source_multitrack.sesx", source_tracks))
    (package_dir / "session_report.json").write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    subtitles = review_package.write_subtitles(output_dir, plan, segments)
    review = review_package.generate_review(ffmpeg, output_dir, master, plan, assets, args.review_limit)
    report_lines.append(
        f"Review package: {review['generated']}/{review['total_speech_splices']} speech splices; not listening-verified. Subtitles: {subtitles['cues']} cues; partial utterances omitted."
    )
    write_markdown_outputs(output_dir, args, assets, edits, report_lines, analysis, music_manifest, intro_duration)

    status = (
        audition_bridge.detect_audition(args.audition_path)
        if (args.enable_audition or args.open_audition or args.attempt_au_import)
        else audition_bridge.AuditionStatus(sys.platform, None, None, False, False, ["Detection not requested."])
    )
    audition_bridge.write_audition_package(
        package_dir,
        status,
        timeline_rows,
        [asset.original_copy for asset in original_assets],
        [asset.cleaned_wav for asset in original_assets],
        master,
    )
    manifest = {
        "final_audio": str(final_path),
        "inputs": [str(path) for path in inputs],
        "clips": [str(path) for path in clip_files],
        "audition": asdict(status),
        "music": music_manifest,
        "master_audio": str(master),
        "qa": qa,
        "input_mode": args.input_mode,
        "sessions": sessions,
        "review": review,
        "subtitles": subtitles,
    }
    (package_dir / "audition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    config = vars(args).copy()
    config.pop("hf_token", None)
    config.pop("jamendo_client_id", None)
    (output_dir / "run_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.open_audition or args.attempt_au_import:
        files_to_open = [package_dir / "podcast_edit.sesx"] if args.attempt_au_import else [master]
        ok, msg = audition_bridge.launch_audition(status, files_to_open)
        report_lines.append(f"Open Audition: {ok} {msg}")
        write_markdown_outputs(output_dir, args, assets, edits, report_lines, analysis, music_manifest, intro_duration)

    if args.generate_intro_outro:
        (output_dir / "intro_outro_script.md").write_text(
            f"# 待录制开场与结尾\n\n此文案未合成人声，也未插入成品。\n\n"
            f"开场：欢迎收听{args.podcast_name or '本期播客'}。本期我们讨论{args.episode_topic or '本次访谈主题'}。\n\n"
            "结尾：感谢收听，也感谢参与本次讨论的朋友。我们下期再见。\n",
            encoding="utf-8",
        )

    release = []
    for path in (final_path, master):
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        release.append({"file": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    (output_dir / "release_manifest.json").write_text(
        json.dumps({"files": release, "qa": qa, "source_identity": source_identity}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    readiness = {
        "technical_checks_passed": technical_pass,
        "full_listening_review_completed": False,
        "editorial_review_completed": False,
        "automatically_published": False,
        "missing_transcript_sources": [
            a.source.name
            for a in assets
            if not any(s.source_file == str(a.source) and s.text.strip() for s in segments)
        ],
        "partial_subtitle_segments": len(subtitles["partial_utterances_omitted"]),
        "speech_splices_not_sampled": review["not_sampled"],
        "status": "awaiting_human_review" if technical_pass else "technical_qa_failed",
    }
    (output_dir / "production_status.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "failure_report.md").unlink(missing_ok=True)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 2 if args.require_technical_qa and not technical_pass else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Produce a publishable multi-speaker interview podcast MVP package.")
    parser.add_argument(
        "--config", help="JSON configuration; CLI overrides it. Relative paths resolve from config directory."
    )
    parser.add_argument(
        "--input", nargs="+", help="Input recordings, concatenated in given order (not synchronized mic tracks)."
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--transcript-json", default="")
    parser.add_argument("--edit-plan", default="")
    parser.add_argument("--speaker-map", type=json.loads, default={})
    parser.add_argument("--input-mode", choices=["sequence", "multitrack"], default="sequence")
    parser.add_argument("--track-offsets", type=json.loads, default={})
    parser.add_argument("--track-gains", type=json.loads, default={})
    parser.add_argument("--track-names", type=json.loads, default={})
    parser.add_argument("--review-limit", type=int, default=20)
    parser.add_argument("--require-technical-qa", action="store_true")
    parser.add_argument("--trim-silence", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--silence-min", type=float, default=2.5)
    parser.add_argument("--output-dir", default="podcast_output")
    parser.add_argument("--podcast-name", default="")
    parser.add_argument("--episode-topic", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--guest", action="append", default=[])
    parser.add_argument(
        "--keep-chitchat",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Do not flag small-talk for review; speech is always preserved by default.",
    )
    parser.add_argument(
        "--generate-intro-outro",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Write recording copy; does not synthesize a voice.",
    )
    parser.add_argument("--target-format", choices=["mp3", "wav"], default="mp3")
    parser.add_argument("--target-lufs", type=float, default=-16.0)
    parser.add_argument("--enable-audition", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--audition-path", default="")
    parser.add_argument("--generate-audition-package", action="store_true", default=True)
    parser.add_argument("--open-audition", action="store_true")
    parser.add_argument("--attempt-au-import", action="store_true")
    parser.add_argument("--install-ai-deps", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--ai-env", choices=["auto", "current", "managed"], default="auto")
    parser.add_argument("--compute", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--whisper-model", choices=["small", "medium", "large-v3", "distil-large-v3"], default="small")
    parser.add_argument("--hf-token", default=os.environ.get("HUGGINGFACE_TOKEN"))
    parser.add_argument("--music-mode", choices=["off", "auto", "provided"], default="off")
    parser.add_argument("--music-style", default="calm modern instrumental interview podcast")
    parser.add_argument("--music-provider", choices=["internet-archive", "jamendo"], default="internet-archive")
    parser.add_argument("--jamendo-client-id", default=os.environ.get("JAMENDO_CLIENT_ID"))
    parser.add_argument("--allow-cc-attribution-music", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--music-file", default="")
    parser.add_argument("--intro-music", default="")
    parser.add_argument("--outro-music", default="")
    parser.add_argument("--music-license", default="")
    parser.add_argument("--music-creator", default="")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    preliminary, _ = parser.parse_known_args(argv)
    if preliminary.config:
        path = Path(preliminary.config).resolve()
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(config, dict):
                raise ValueError("Configuration must be a JSON object.")
            config = {key.replace("-", "_"): value for key, value in config.items()}
            actions = {a.dest: a for a in parser._actions}
            if "config" in config or "help" in config:
                raise ValueError("Nested config and help keys are not supported.")
            if set(config) - set(actions):
                raise ValueError(f"Unknown configuration keys: {', '.join(sorted(set(config) - set(actions)))}")
            if "hf_token" in config or "jamendo_client_id" in config:
                raise ValueError("Store credentials in environment variables, not configuration files.")
            for key, value in config.items():
                action = actions[key]
                if isinstance(action.default, str) and not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
                if isinstance(action, (argparse._StoreTrueAction, argparse.BooleanOptionalAction)) and not isinstance(
                    value, bool
                ):
                    raise ValueError(f"{key} must be a boolean")
                if action.choices and value not in action.choices:
                    raise ValueError(f"Unsupported value for {key}")
            for key in ("input", "guest"):
                if isinstance(config.get(key), str):
                    config[key] = [config[key]]
                if key in config and (
                    not isinstance(config[key], list) or not all(isinstance(v, str) for v in config[key])
                ):
                    raise ValueError(f"{key} must be a list of strings")
            path_keys = (
                "output_dir",
                "transcript_json",
                "edit_plan",
                "music_file",
                "intro_music",
                "outro_music",
                "audition_path",
            )
            for key in path_keys:
                if config.get(key):
                    config[key] = str((path.parent / config[key]).resolve())
            if "input" in config:
                config["input"] = [str((path.parent / v).resolve()) for v in config["input"]]
            if "--guest" in argv:
                config["guest"] = []
            parser.set_defaults(**config)
        except (OSError, ValueError, TypeError) as exc:
            parser.error(f"Cannot load configuration: {exc}")
    args = parser.parse_args(argv)
    if not args.input:
        parser.error("Supply --input or input in --config.")
    if not isinstance(args.speaker_map, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in args.speaker_map.items()
    ):
        parser.error("speaker_map must be a JSON object mapping speaker labels to names.")
    if (
        not isinstance(args.target_lufs, (int, float))
        or isinstance(args.target_lufs, bool)
        or not math.isfinite(args.target_lufs)
        or not -30 <= args.target_lufs <= -10
    ):
        parser.error("target_lufs must be between -30 and -10.")
    if (
        not isinstance(args.silence_min, (int, float))
        or isinstance(args.silence_min, bool)
        or not math.isfinite(args.silence_min)
        or args.silence_min < 1.5
    ):
        parser.error("silence_min must be at least 1.5 seconds.")
    if args.skip_ai and args.install_ai_deps:
        parser.error("--skip-ai cannot be combined with --install-ai-deps.")
    if args.install_ai_deps and args.ai_env == "current":
        parser.error("--install-ai-deps creates a managed environment; select --ai-env auto or managed.")
    for name in ("track_offsets", "track_gains", "track_names"):
        if not isinstance(getattr(args, name), dict) or not all(isinstance(k, str) for k in getattr(args, name)):
            parser.error(f"{name} must be a JSON object with string keys.")
    if args.input_mode == "sequence" and (args.track_offsets or args.track_gains or args.track_names):
        parser.error("Track settings require --input-mode multitrack.")
    if (
        isinstance(args.review_limit, bool)
        or not isinstance(args.review_limit, int)
        or not 0 <= args.review_limit <= 100
    ):
        parser.error("review_limit must be an integer from 0 to 100.")
    return args


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        return prepare_audio(args)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        message = str(exc)
        for secret in (args.hf_token, args.jamendo_client_id):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

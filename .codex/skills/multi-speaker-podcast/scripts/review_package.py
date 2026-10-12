"""Bounded splice listening checks and conservative final-timeline subtitles."""

from __future__ import annotations

import html
import json
from pathlib import Path

import audio_editing


def timestamp(value: float, separator: str = ".") -> str:
    milliseconds = max(0, round(value * 1000))
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def write_subtitles(output: Path, plan: dict, segments) -> dict:
    cues, partial = [], []
    for clip in plan["clips"]:
        for segment in segments:
            if segment.source_file != clip["source_file"] or not segment.text.strip():
                continue
            if min(segment.end, clip["end"]) <= max(segment.start, clip["start"]):
                continue
            if segment.start < clip["start"] - 0.005 or segment.end > clip["end"] + 0.005:
                partial.append(
                    {
                        "clip_id": clip["clip_id"],
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text,
                        "reason": "Cut intersects utterance; not safe to infer retained words.",
                    }
                )
                continue
            start = clip["timeline_start"] + max(0, segment.start - clip["start"])
            end = min(clip["timeline_end"], clip["timeline_start"] + segment.end - clip["start"])
            text = " ".join(segment.text.split()).replace("-->", " ").replace("<", "").replace(">", "")
            cues.append({"start": start, "end": end, "text": f"{segment.speaker}: {text}"})
    cues.sort(key=lambda row: row["start"])
    srt, vtt = [], ["WEBVTT", ""]
    for index, cue in enumerate(cues, 1):
        srt.extend([str(index), f"{timestamp(cue['start'], ',')} --> {timestamp(cue['end'], ',')}", cue["text"], ""])
        vtt.extend([f"{timestamp(cue['start'])} --> {timestamp(cue['end'])}", cue["text"], ""])
    (output / "final_subtitles.srt").write_text("\n".join(srt), encoding="utf-8")
    (output / "final_subtitles.vtt").write_text("\n".join(vtt), encoding="utf-8")
    result = {
        "coordinate_system": "final_seconds",
        "cues": len(cues),
        "partial_utterances_omitted": partial,
        "notes": [
            "Subtitle text is ASR/external transcript, not verified by listening.",
            "No speech was invented for intro/outro music or partial utterances.",
        ],
    }
    (output / "subtitle_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def excerpt(ffmpeg: str, source: Path, start: float, end: float, target: Path):
    if end <= start:
        return
    audio_editing.execute(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(max(0, start)),
            "-i",
            str(source),
            "-t",
            str(end - max(0, start)),
            "-vn",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )


def generate_review(ffmpeg: str, output: Path, master: Path, plan: dict, assets, limit: int) -> dict:
    directory = output / "review_package"
    directory.mkdir(exist_ok=True)
    by_source = {str(asset.source): asset for asset in assets}
    pairs = list(zip(plan["clips"], plan["clips"][1:]))
    rows, panels = [], []
    for index, (left, right) in enumerate(pairs[:limit], 1):
        boundary = right["timeline_start"]
        prefix = f"splice_{index:03}"
        before = directory / f"{prefix}_left_original.wav"
        after = directory / f"{prefix}_right_original.wav"
        result = directory / f"{prefix}_edited.wav"
        a, b = by_source[left["source_file"]], by_source[right["source_file"]]
        # Original endpoints are separate players: reorder/switch cannot imply original continuity.
        excerpt(ffmpeg, a.working_wav, max(left["start"], left["end"] - 2), left["end"], before)
        excerpt(ffmpeg, b.working_wav, right["start"], min(right["end"], right["start"] + 2), after)
        excerpt(
            ffmpeg, master, max(left["timeline_start"], boundary - 2), min(right["timeline_end"], boundary + 2), result
        )
        row = {
            "splice_id": prefix,
            "final_time": boundary,
            "left_source": a.source.name,
            "left_end": left["end"],
            "right_source": b.source.name,
            "right_start": right["start"],
            "left_original": before.name,
            "right_original": after.name,
            "edited": result.name,
            "listening_verified": False,
        }
        rows.append(row)
        players = "".join(
            f'<div><span>{label}</span><audio controls preload="none" src="{file}"></audio></div>'
            for label, file in (("左侧源参考", before.name), ("右侧源参考", after.name), ("成品衔接", result.name))
        )
        panels.append(
            f"<section><h2>{index:02} · {timestamp(boundary)}</h2><p>{html.escape(a.source.name)} "
            f"{left['end']:.3f}s → {html.escape(b.source.name)} {right['start']:.3f}s</p>{players}</section>"
        )
    report = {
        "total_speech_splices": len(pairs),
        "generated": len(rows),
        "not_sampled": max(0, len(pairs) - limit),
        "listening_verified": False,
        "checks": rows,
        "note": "Speech splices only. Intro/outro transitions and full-episode listening still require review. Multitrack source references use the aligned preprocessed mix, not isolated raw microphone audio.",
    }
    (directory / "review_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Standalone local report; all media stays inside this directory.
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += "<title>播客剪点复听</title><style>body{margin:0;background:#f6f7f8;color:#25282b;font:16px system-ui,sans-serif;letter-spacing:0}main{max-width:960px;margin:auto;padding:24px}h1{font-size:26px}h2{font-size:18px}p{overflow-wrap:anywhere;color:#575e65}section{border-top:1px solid #cbd1d6;padding:16px 0}section div{display:grid;grid-template-columns:100px minmax(0,1fr);align-items:center;margin:8px 0}audio{width:100%;max-width:600px}@media(max-width:480px){section div{grid-template-columns:1fr;gap:6px}}</style><main>"
    page += f"<h1>播客剪点复听</h1><p>已生成 {len(rows)} / {len(pairs)} 个语音剪点，尚未人工确认。片头片尾衔接和整期内容仍需复听。</p>"
    page += "".join(panels) or "<p>本次没有语音片段间的剪点。</p>"
    page += "</main></html>"
    (directory / "index.html").write_text(page, encoding="utf-8")
    return report

"""Independent structural, media and manifest acceptance checks for an episode."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import audio_editing
import podcast_pipeline as pipeline

REQUIRED = [
    "master_podcast.wav",
    "transcript_raw.md",
    "transcript_cleaned.md",
    "transcript.json",
    "podcast_structure.md",
    "edit_decision_list.md",
    "edit_decision_list.json",
    "edit_report.md",
    "show_notes.md",
    "source_audio_info.json",
    "audio_info_report.md",
    "content_scores.json",
    "audio_qa.json",
    "audio_qa_report.md",
    "music_manifest.json",
    "music_license_report.md",
    "run_config.json",
    "production_status.json",
    "run_state.json",
    "release_manifest.json",
    "final_subtitles.srt",
    "final_subtitles.vtt",
    "subtitle_report.json",
    "audition_timeline.csv",
    "audition_package/audition_manifest.json",
    "audition_package/audition_status.json",
    "audition_package/audition_edit_plan.md",
    "audition_package/audition_timeline.csv",
    "audition_package/audition_markers.csv",
    "audition_package/podcast_edit.sesx",
    "audition_package/session_report.json",
    "review_package/index.html",
    "review_package/review_manifest.json",
]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def time_seconds(value: str) -> float:
    result = 0.0
    for part in value.replace(",", ".").split(":"):
        result = result * 60 + float(part)
    return result


class MediaReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "audio":
            self.sources.append(dict(attrs).get("src", ""))


def audit(output: Path, decode: bool = True) -> dict:
    if not __debug__:
        return {
            "passed": False,
            "checks": [
                {
                    "name": "validation_enabled",
                    "passed": False,
                    "detail": "Run without Python -O/PYTHONOPTIMIZE; assertion-based acceptance checks must remain enabled.",
                }
            ],
            "warnings": [],
        }
    checks, warnings = [], []

    def check(name, action):
        try:
            detail = action()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": str(exc) or type(exc).__name__})

    def require_files():
        missing = [name for name in REQUIRED if not (output / name).is_file()]
        assert not missing, f"Missing: {missing}"
        return len(REQUIRED)

    check("required_artifacts", require_files)
    ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")

    def parse_json_artifacts():
        def finite(value):
            if isinstance(value, dict):
                for item in value.values():
                    finite(item)
            elif isinstance(value, list):
                for item in value:
                    finite(item)
            elif isinstance(value, float):
                assert math.isfinite(value), "Non-finite JSON number"

        for name in REQUIRED:
            if name.endswith(".json"):
                finite(json.loads((output / name).read_text(encoding="utf-8")))
        return "All required JSON files parse and contain no NaN/Infinity"

    check("all_json_artifacts", parse_json_artifacts)

    def consistency():
        state = json.loads((output / "run_state.json").read_text(encoding="utf-8"))
        assert state["status"] == "completed", f"Run is not completed: {state['status']}"
        for name in ("release_manifest.json", "production_status.json", "audition_package/audition_manifest.json"):
            data = json.loads((output / name).read_text(encoding="utf-8"))
            assert data["run_id"] == state["run_id"], f"Stale manifest: {name}"
            assert data["status"] not in {"failed", "running", "technical_qa_failed"}, name
        return state["run_id"]

    check("run_identity_and_status", consistency)

    def hashes():
        manifest = json.loads((output / "release_manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["files"]) == 2, "Release requires master plus release file."
        for row in manifest["files"]:
            path = (output / row["file"]).resolve()
            assert path.is_relative_to(output.resolve()), "Manifest path escapes episode directory."
            assert path.stat().st_size == row["bytes"] and digest(path) == row["sha256"], row["file"]
        return "SHA256 and sizes match"

    check("release_hashes", hashes)

    def master_format():
        assert ffprobe, "ffprobe missing"
        info = pipeline.ffprobe_info(ffprobe, output / "master_podcast.wav")
        stream = next(row for row in info["streams"] if row.get("codec_type") == "audio")
        assert stream["codec_name"] == "pcm_s24le" and int(stream["sample_rate"]) == 48000
        assert stream["channels"] == 2 and int(stream["bits_per_sample"]) == 24
        qa = json.loads((output / "audio_qa.json").read_text(encoding="utf-8"))
        assert qa["decode_ok"] and qa["loudness_within_1_lu"] and qa["peak_below_minus_1_dbtp"]
        assert abs(qa["integrated_lufs"] - qa["target_lufs"]) <= 1 and qa["true_peak_dbtp"] <= -1
        return "48kHz stereo PCM24 master; saved release loudness/peak measurements passed"

    check("master_format_and_saved_qa", master_format)

    def timeline():
        plan = json.loads((output / "edit_decision_list.json").read_text(encoding="utf-8"))
        cursor = plan["intro_duration"]
        with (output / "audition_timeline.csv").open(encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        assert len(rows) == len(plan["clips"]), "CSV/JSON clip count mismatch"
        assert (output / "audition_timeline.csv").read_bytes() == (
            output / "audition_package/audition_timeline.csv"
        ).read_bytes()
        assert ffprobe, "ffprobe missing"
        for row, clip in zip(rows, plan["clips"]):
            assert abs(clip["timeline_start"] - cursor) < 0.005, "Timeline gap/overlap"
            assert abs(time_seconds(row["timeline_start"]) - cursor) < 0.005, "CSV timestamp mismatch"
            assert math.isfinite(clip["duration"]) and clip["duration"] > 0
            path = output / "audition_package/clips" / f"{clip['clip_id']}.wav"
            assert abs(pipeline.duration(ffprobe, path) - clip["duration"]) < 0.02, clip["clip_id"]
            cursor += clip["duration"]
        assert abs(cursor - plan["speech_timeline_end"]) < 0.005
        expected = cursor
        music = json.loads((output / "music_manifest.json").read_text(encoding="utf-8"))
        if music.get("auto_mix_ready") and music.get("outro_music"):
            expected += pipeline.duration(ffprobe, output / "audition_package/music/outro_music.wav")
        assert abs(pipeline.duration(ffprobe, output / "master_podcast.wav") - expected) < 0.05, (
            "Master duration mismatch"
        )
        return {"clips": len(rows), "master_seconds": expected}

    check("timeline_and_media_duration", timeline)

    def sessions():
        package = output / "audition_package"
        reports = json.loads((package / "session_report.json").read_text(encoding="utf-8"))
        for report in reports:
            tree = ET.parse(package / report["file"])
            files = {row.get("id"): row for row in tree.findall("./files/file")}
            clips = tree.findall("./session/tracks/audioTrack/audioClip")
            assert len(clips) == report["clips"]
            for clip in clips:
                path = (package / files[clip.get("fileID")].get("relativePath")).resolve()
                assert path.is_relative_to(package.resolve()) and path.is_file(), (
                    "Broken/escaping relative media reference"
                )
                assert int(clip.get("endPoint")) > int(clip.get("startPoint")) >= 0
                assert int(clip.get("sourceOutPoint")) > int(clip.get("sourceInPoint")) >= 0
        config = json.loads((output / "run_config.json").read_text(encoding="utf-8"))
        if config["input_mode"] == "multitrack":
            assert (package / "source_multitrack.sesx").is_file() and (output / "multitrack_report.json").is_file()
        assert any((package / name).is_file() for name in ("open_in_audition.ps1", "open_in_audition.sh"))
        return f"{len(reports)} sessions; XML/media only, not AU GUI validation"

    check("audition_sessions_and_relative_media", sessions)

    def review():
        parser = MediaReferences()
        parser.feed((output / "review_package/index.html").read_text(encoding="utf-8"))
        report = json.loads((output / "review_package/review_manifest.json").read_text(encoding="utf-8"))
        assert len(parser.sources) == report["generated"] * 3
        for name in parser.sources:
            path = (output / "review_package" / name).resolve()
            assert path.is_relative_to((output / "review_package").resolve()) and path.is_file()
            assert not ffprobe or pipeline.duration(ffprobe, path) > 0
        return f"{report['generated']} splices; HTML media links only, not visual/listening validation"

    check("review_audio_references", review)

    def subtitles():
        text = (output / "final_subtitles.srt").read_text(encoding="utf-8")
        rows = re.findall(r"(\d+:\d+:\d+,\d+) --> (\d+:\d+:\d+,\d+)", text)
        assert ffprobe
        total = pipeline.duration(ffprobe, output / "master_podcast.wav")
        for start, end in rows:
            assert 0 <= time_seconds(start) < time_seconds(end) <= total + 0.01
        report = json.loads((output / "subtitle_report.json").read_text(encoding="utf-8"))
        assert len(rows) == report["cues"]
        return {"cues": len(rows), "partial_utterances": len(report["partial_utterances_omitted"])}

    check("subtitle_timestamps", subtitles)

    def original_copies():
        info = json.loads((output / "source_audio_info.json").read_text(encoding="utf-8"))
        originals = sorted((output / "audition_package/original_audio").iterdir())
        assert len(originals) >= len(info)
        for index, row in enumerate(info, 1):
            name = f"{index:02d}_{Path(row['source_file']).name}"
            copy = output / "audition_package/original_audio" / name
            assert copy.is_file(), name
            source = Path(row["source_file"])
            if source.is_file():
                assert digest(source) == digest(copy), f"Original copy differs: {name}"
        return len(info)

    check("original_audio_preservation", original_copies)

    if decode:

        def decode_release():
            assert ffmpeg
            manifest = json.loads((output / "release_manifest.json").read_text(encoding="utf-8"))
            for row in manifest["files"]:
                audio_editing.execute([ffmpeg, "-v", "error", "-i", str(output / row["file"]), "-f", "null", "-"])
            return "Full master and release decode passed"

        check("full_audio_decode", decode_release)

    warnings.append(
        "Structural acceptance does not verify ASR accuracy, editing meaning, licenses, AU GUI or full listening."
    )
    try:
        readiness = json.loads((output / "production_status.json").read_text(encoding="utf-8"))
        if isinstance(readiness, dict) and readiness.get("missing_transcript_sources"):
            warnings.append(f"Missing transcripts: {readiness['missing_transcript_sources']}")
    except (OSError, ValueError):
        warnings.append("Production status missing or malformed; see failed checks.")
    return {"passed": all(row["passed"] for row in checks), "checks": checks, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit(args.output.resolve())
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

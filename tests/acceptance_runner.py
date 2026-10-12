"""Repeatable local format/stress/AI acceptance matrix; preserves all evidence."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".codex/skills/multi-speaker-podcast/scripts"
sys.path.insert(0, str(SCRIPTS))
import setup_ai_env
import validate_output


def execute(command, timeout=600):
    return subprocess.run(
        list(map(str, command)), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def generate(path, seconds=3.5, rate=48000, channels=2, frequency=440):
    result = execute(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-ar",
            rate,
            "-ac",
            channels,
            path,
        ]
    )
    if result.returncode:
        raise RuntimeError(result.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-ai", action="store_true")
    parser.add_argument("--stress-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.stress_seconds <= 1800:
        parser.error("stress-seconds must be 10..1800")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / ".test-artifacts/acceptance" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixtures = output / "fixtures"
    fixtures.mkdir()
    cases = []

    def case(name, action):
        print(f"Acceptance: {name}", flush=True)
        started = time.monotonic()
        try:
            detail = action()
            passed = True
        except Exception as exc:
            detail, passed = str(exc) or type(exc).__name__, False
        row = {"name": name, "passed": passed, "seconds": round(time.monotonic() - started, 3), "detail": detail}
        cases.append(row)
        (output / "progress.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    def render(name, inputs, extra=None, ai=False, expected=0):
        target = output / name
        command = [sys.executable, SCRIPTS / "podcast_pipeline.py", "--input", *inputs, "--output-dir", target]
        if not ai:
            command.append("--skip-ai")
        command += extra or []
        result = execute(command, timeout=900)
        (output / f"{name}.log").write_text(result.stdout + "\nSTDERR\n" + result.stderr, encoding="utf-8")
        if result.returncode != expected:
            raise RuntimeError(f"Exit {result.returncode}; expected {expected}: {result.stderr[-1500:]}")
        if expected == 0:
            audit = validate_output.audit(target)
            (output / f"{name}_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if not audit["passed"]:
                raise RuntimeError(json.dumps([c for c in audit["checks"] if not c["passed"]], ensure_ascii=False))
            return {
                "output": str(target),
                "checks": len(audit["checks"]),
                "qa": json.loads((target / "audio_qa.json").read_text(encoding="utf-8")),
            }
        state = json.loads((target / "production_status.json").read_text(encoding="utf-8"))
        assert state["status"] == "technical_qa_failed"
        return state

    formats = [
        ("wav_8k_mono", "wav", 8000, 1),
        ("mp3_44k_stereo", "mp3", 44100, 2),
        ("flac_48k_mono", "flac", 48000, 1),
        ("m4a_32k_stereo", "m4a", 32000, 2),
        ("wav_96k_6channels", "wav", 96000, 6),
        ("ogg_48k_stereo", "ogg", 48000, 2),
    ]
    for name, extension, rate, channels in formats:
        source = fixtures / f"采访 ' $ {name}.{extension}"
        generate(source, rate=rate, channels=channels)
        case(name, lambda n=name, s=source: render(n, [s], ["--require-technical-qa"]))

    short = fixtures / "tiny.wav"
    generate(short, seconds=0.06)
    case(
        "very_short_audio",
        lambda: render("very_short_audio", [short], ["--no-trim-silence", "--require-technical-qa"], expected=2),
    )

    source = fixtures / "many_clips.wav"
    generate(source, seconds=10)
    plan = fixtures / "many_clips.json"
    plan.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "source_file": source.name,
                        "start": index % 8,
                        "end": index % 8 + 1,
                        "reason": "Synthetic stress fixture",
                    }
                    for index in range(30)
                ]
            }
        ),
        encoding="utf-8",
    )
    case(
        "thirty_reordered_clips",
        lambda: render(
            "thirty_reordered_clips",
            [source],
            ["--edit-plan", plan, "--review-limit", "3", "--target-format", "wav", "--require-technical-qa"],
        ),
    )

    def music_fallback():
        detail = render(
            "music_provider_unconfigured_fallback",
            [source],
            [
                "--music-mode",
                "auto",
                "--music-provider",
                "jamendo",
                "--jamendo-client-id",
                "",
                "--require-technical-qa",
            ],
        )
        manifest = json.loads(
            (output / "music_provider_unconfigured_fallback/music_manifest.json").read_text(encoding="utf-8")
        )
        assert not manifest["auto_mix_ready"] and manifest["errors"], "Provider failure was not recorded"
        return detail

    case("music_provider_unconfigured_fallback", music_fallback)

    tracks = [fixtures / f"stress_mic_{i}.wav" for i in range(1, 4)]
    for index, track in enumerate(tracks):
        generate(track, seconds=args.stress_seconds, frequency=220 + index * 110)
    case(
        "three_track_long_mix",
        lambda: render(
            "three_track_long_mix",
            tracks,
            [
                "--input-mode",
                "multitrack",
                "--track-offsets",
                '{"2":0.125,"3":0.25}',
                "--track-gains",
                '{"2":-3,"3":-6}',
                "--no-trim-silence",
                "--require-technical-qa",
            ],
        ),
    )

    if args.with_ai:

        def dependencies():
            report = setup_ai_env.setup_env(
                argparse.Namespace(env_dir=str(setup_ai_env.DEFAULT_ENV), compute="auto", dry_run=False)
            )
            setup_ai_env.write_report(report, output / "dependency_report.md")
            assert report.success, report.validation
            return report.validation

        case("managed_ai_environment", dependencies)

        def ai_episode():
            speech = ROOT / ".test-artifacts/ai_smoke.wav"
            assert speech.is_file(), "Synthetic speech fixture ai_smoke.wav missing; AI episode not tested."
            detail = render(
                "cuda_full_episode",
                [speech],
                [
                    "--install-ai-deps",
                    "--compute",
                    "cuda",
                    "--music-mode",
                    "provided",
                    "--music-file",
                    source,
                    "--music-license",
                    "Synthetic test fixture owned by this project",
                    "--generate-intro-outro",
                    "--enable-audition",
                    "--require-technical-qa",
                ],
                ai=True,
            )
            worker = next((output / "cuda_full_episode/work/analysis").glob("*_transcript.json"))
            transcript = json.loads(worker.read_text(encoding="utf-8"))
            assert transcript["device"] == "cuda", "GPU fallback occurred; CUDA acceptance did not pass."
            assert any(row.get("text", "").strip() for row in transcript["segments"]), "ASR returned no text"
            return detail

        case("cuda_full_episode", ai_episode)

    limits = [
        "真实多人 pyannote 模型推理：需 Hugging Face token 与模型授权，未作本次验收。",
        "语义评分是启发式，主题重排和嘉宾原意仍需人工审核。",
        "AU 验收仅检查 XML、时间轴和媒体引用，不代表 GUI 导入或自动后期成功。",
        "音乐来源下载/版权真实性、真实长访谈的 ASR 准确率及整期听感未通过本矩阵验证。",
        "HTML 只做结构与音频链接检查，未进行浏览器视觉验收。",
        "长音频压力素材是合成波形，不代表多人真实访谈的编辑质量。",
    ]
    report = {
        "timestamp": stamp,
        "platform": platform.platform(),
        "python": sys.version,
        "ffmpeg": shutil.which("ffmpeg"),
        "cases": cases,
        "passed": all(row["passed"] for row in cases),
        "stress_seconds_per_track": args.stress_seconds,
        "limits": limits,
    }
    (output / "acceptance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 稳定性与完整度验收",
        "",
        f"- 时间：{stamp}",
        f"- 场景通过：{sum(c['passed'] for c in cases)}/{len(cases)}",
        f"- 压力素材：3 条各 {args.stress_seconds} 秒同步音轨",
        "",
        "| 场景 | 通过 | 耗时（秒） |",
        "|---|---|---:|",
    ]
    lines += [f"| {c['name']} | {c['passed']} | {c['seconds']} |" for c in cases]
    lines += ["", "## 验收边界", "", *[f"- {limit}" for limit in limits]]
    (output / "acceptance_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {output / 'acceptance_report.md'}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

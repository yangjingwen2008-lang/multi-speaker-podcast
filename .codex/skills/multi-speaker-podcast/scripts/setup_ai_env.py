#!/usr/bin/env python3
"""Create and validate the optional AI runtime for podcast transcription/diarization."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import run_state

DEFAULT_ENV = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexPodcastSkill" / "venvs" / "podcast-ai"


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class SetupReport:
    env_dir: str
    python: str | None
    platform: str
    compute_requested: str
    nvidia_smi_found: bool
    cuda_requested: bool
    commands: list[CommandResult]
    validation: dict[str, str]
    success: bool
    dry_run: bool = False


def run(cmd: list[str], timeout: int | None = None) -> CommandResult:
    print(f"AI setup: {subprocess.list2cmdline(cmd)}", flush=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return CommandResult(cmd, proc.returncode, proc.stdout, proc.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return CommandResult(cmd, 124, "", str(exc))


def venv_python(env_dir: Path) -> Path:
    if platform.system() == "Windows":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None and run(["nvidia-smi"], timeout=15).returncode == 0


def choose_compute(requested: str) -> tuple[bool, str]:
    if requested == "cpu":
        return False, "cpu"
    if requested == "cuda":
        return True, "cuda"
    detected = has_nvidia()
    return detected, "cuda" if detected else "cpu"


def validate(py: Path) -> dict[str, str]:
    code = r"""
import importlib.util, json
result = {}
for name in ["torch", "torchaudio", "faster_whisper", "pyannote.audio"]:
    try:
        mod = importlib.import_module(name)
        result[name] = getattr(mod, "__version__", "imported")
    except Exception as exc:
        result[name] = "ERROR: " + repr(exc)
try:
    import torch
    result["torch.cuda.is_available"] = str(torch.cuda.is_available())
    result["torch.version.cuda"] = str(torch.version.cuda)
    if torch.cuda.is_available():
        value = torch.ones(4, device="cuda")
        result["torch.cuda.smoke_test"] = str(float((value * value).sum().item()) == 4)
except Exception as exc:
    result["torch.cuda"] = "ERROR: " + repr(exc)
print(json.dumps(result, ensure_ascii=False, indent=2))
"""
    proc = run([str(py), "-c", "import importlib, json\n" + code], timeout=240)
    if proc.returncode != 0:
        return {"validation_error": "ERROR: " + (proc.stderr.strip() or proc.stdout.strip())}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"validation_output": "ERROR: " + proc.stdout}


def validation_ok(validation: dict[str, str]) -> bool:
    required = {"torch", "torchaudio", "faster_whisper", "pyannote.audio"}
    return required.issubset(validation) and all(not str(v).startswith("ERROR:") for v in validation.values())


def write_report(report: SetupReport, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Podcast AI Dependency Report",
        "",
        f"- Environment: `{report.env_dir}`",
        f"- Python: `{report.python or 'not created'}`",
        f"- Platform: `{report.platform}`",
        f"- Compute requested: `{report.compute_requested}`",
        f"- NVIDIA detected: `{report.nvidia_smi_found}`",
        f"- CUDA install attempted: `{report.cuda_requested}`",
        f"- Success: `{report.success}`",
        f"- Dry run (no installation): `{report.dry_run}`",
        "",
        "## Validation",
    ]
    for key, value in report.validation.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Commands"])
    for item in report.commands:
        lines.append(f"### `{' '.join(item.command)}`")
        lines.append(f"- Return code: `{item.returncode}`")
        if item.stdout.strip():
            lines.append("```text")
            lines.append(item.stdout.strip()[-4000:])
            lines.append("```")
        if item.stderr.strip():
            lines.append("```text")
            lines.append(item.stderr.strip()[-4000:])
            lines.append("```")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def setup_env(args: argparse.Namespace) -> SetupReport:
    if args.dry_run:
        return setup_env_unlocked(args)
    env_dir = Path(args.env_dir).expanduser().resolve()
    lock_dir = env_dir.parent / f".{env_dir.name}.setup-lock"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        with run_state.OutputLock(lock_dir):
            return setup_env_unlocked(args)
    except (run_state.ConcurrentRunError, OSError) as exc:
        return SetupReport(
            str(env_dir),
            None,
            platform.platform(),
            args.compute,
            False,
            args.compute == "cuda",
            [],
            {"validation_error": f"ERROR: AI environment setup unavailable: {exc}"},
            False,
        )


def setup_env_unlocked(args: argparse.Namespace) -> SetupReport:
    env_dir = Path(args.env_dir).expanduser()
    commands: list[CommandResult] = []
    nvidia = has_nvidia()
    use_cuda, _label = choose_compute(args.compute)
    uv = shutil.which("uv")
    if not uv:
        return SetupReport(
            str(env_dir),
            None,
            platform.platform(),
            args.compute,
            nvidia,
            use_cuda,
            [],
            {"validation_error": "ERROR: uv is required; install uv and rerun."},
            False,
        )

    py = venv_python(env_dir)
    if args.dry_run:
        report = SetupReport(
            str(env_dir), str(py), platform.platform(), args.compute, nvidia, use_cuda, [], {}, True, True
        )
        return report

    env_dir.parent.mkdir(parents=True, exist_ok=True)
    if py.exists():
        existing = validate(py)
        compute_ok = args.compute != "cuda" or existing.get("torch.cuda.is_available") == "True"
        if validation_ok(existing) and compute_ok:
            return SetupReport(
                str(env_dir), str(py), platform.platform(), args.compute, nvidia, use_cuda, [], existing, True
            )
    if not py.exists():
        commands.append(run([uv, "venv", "--python", "3.11", str(env_dir)], timeout=600))
        if commands[-1].returncode != 0:
            report = SetupReport(
                str(env_dir), None, platform.platform(), args.compute, nvidia, use_cuda, commands, {}, False
            )
            return report

    torch_cmds: list[list[str]] = []
    if use_cuda:
        torch_cmds.append(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(py),
                "torch==2.8.0",
                "torchaudio==2.8.0",
                "--index-url",
                "https://download.pytorch.org/whl/cu128",
                "--reinstall-package",
                "torch",
                "--reinstall-package",
                "torchaudio",
            ]
        )
    torch_cmds.append(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(py),
            "torch==2.8.0",
            "torchaudio==2.8.0",
            "--index-url",
            "https://download.pytorch.org/whl/cpu",
            "--reinstall-package",
            "torch",
            "--reinstall-package",
            "torchaudio",
        ]
    )

    torch_ok = False
    for cmd in torch_cmds:
        result = run(cmd, timeout=3600)
        commands.append(result)
        if result.returncode == 0:
            torch_ok = True
            break

    if torch_ok:
        versions = run(
            [
                str(py),
                "-c",
                "import importlib.metadata as m; print('torch==' + m.version('torch')); print('torchaudio==' + m.version('torchaudio'))",
            ],
            timeout=30,
        )
        constraints = env_dir / "podcast-torch-constraints.txt"
        if versions.returncode:
            commands.append(versions)
            return SetupReport(
                str(env_dir),
                str(py),
                platform.platform(),
                args.compute,
                nvidia,
                use_cuda,
                commands,
                {"validation_error": "ERROR: PyTorch version detection failed."},
                False,
            )
        constraints.write_text(versions.stdout, encoding="utf-8")
        commands.append(
            run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(py),
                    "faster-whisper",
                    "pyannote.audio>=4,<5",
                    "matplotlib>=3.10,<3.11",
                    "torchcodec>=0.7,<0.8",
                    "--constraint",
                    str(constraints),
                ],
                timeout=3600,
            )
        )

    validation = validate(py) if py.exists() else {}
    if use_cuda and validation.get("torch.cuda.is_available") == "False" and torch_ok:
        result = run(torch_cmds[-1], timeout=3600)
        commands.append(result)
        validation = validate(py)
    success = py.exists() and validation_ok(validation)
    return SetupReport(
        str(env_dir),
        str(py) if py.exists() else None,
        platform.platform(),
        args.compute,
        nvidia,
        use_cuda,
        commands,
        validation,
        success,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install optional AI dependencies for the podcast skill.")
    parser.add_argument("--env-dir", default=str(DEFAULT_ENV))
    parser.add_argument("--compute", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--report", default="dependency_report.md")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = setup_env(args)
    write_report(report, Path(args.report))
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())

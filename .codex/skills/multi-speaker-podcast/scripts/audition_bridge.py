#!/usr/bin/env python3
"""Adobe Audition detection and handoff helpers for the podcast skill."""

from __future__ import annotations

import csv
import json
import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class AuditionStatus:
    system: str
    executable: str | None
    detected_from: str | None
    can_launch: bool
    can_auto_import: bool
    notes: list[str]


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _process_paths_windows() -> list[Path]:
    paths: list[Path] = []
    try:
        result = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -like '*Audition*.exe' } | "
                    "ForEach-Object { $_.ExecutablePath }"
                ),
            ]
        )
    except Exception:
        return paths
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and Path(line).exists():
            paths.append(Path(line))
    return paths


def _candidate_paths_windows() -> Iterable[Path]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        base = Path(root)
        if not base.exists():
            continue
        for pattern in [
            "Adobe/Adobe Audition */Adobe Audition.exe",
            "Adobe*/Adobe Audition */Adobe Audition.exe",
            "Adobe Audition */Adobe Audition.exe",
        ]:
            yield from base.glob(pattern)


def _registered_paths_windows() -> list[Path]:
    import winreg

    results = []
    key_name = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Adobe Audition.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                path = Path(os.path.expandvars(value.strip('"')))
                if path.is_file():
                    results.append(path)
            except OSError:
                continue
    return results


def _candidate_paths_macos() -> Iterable[Path]:
    yield Path("/Applications/Adobe Audition 2026/Adobe Audition 2026.app")
    yield Path("/Applications/Adobe Audition 2025/Adobe Audition 2025.app")
    yield Path("/Applications/Adobe Audition 2024/Adobe Audition 2024.app")
    yield Path("/Applications/Adobe Audition 2023/Adobe Audition 2023.app")
    yield from Path("/Applications").glob("Adobe Audition*/Adobe Audition*.app")


def detect_audition(explicit_path: str | None = None) -> AuditionStatus:
    system = platform.system()
    notes: list[str] = []

    if explicit_path:
        path = Path(explicit_path)
        if path.is_file() or (system == "Darwin" and path.is_dir() and path.suffix == ".app"):
            return AuditionStatus(system, str(path), "explicit_path", True, True, notes)
        notes.append(f"Explicit Adobe Audition path was not found: {explicit_path}")
        return AuditionStatus(system, None, "explicit_path", False, False, notes)

    if system == "Windows":
        paths = _process_paths_windows()
        if paths:
            return AuditionStatus(
                system,
                str(paths[0]),
                "running_process",
                True,
                True,
                ["File-open dispatch supported; multitrack placement and editing are not verified automation."],
            )
        paths = _registered_paths_windows()
        if paths:
            return AuditionStatus(
                system,
                str(paths[0]),
                "registry_app_paths",
                True,
                True,
                ["File-open dispatch supported; import and editing completion are not verified."],
            )

    candidates: list[Path] = []
    if system == "Windows":
        candidates.extend(_candidate_paths_windows())
    elif system == "Darwin":
        candidates.extend(_candidate_paths_macos())
    else:
        notes.append(f"Automatic Adobe Audition detection is not implemented for {system}.")

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            source = "filesystem_scan"
            return AuditionStatus(system, str(candidate), source, True, True, notes)

    if system == "Darwin" and shutil.which("open"):
        notes.append("Audition app was not found, but macOS open command is available.")

    return AuditionStatus(system, None, None, False, False, notes)


def write_audition_package(
    package_dir: Path,
    status: AuditionStatus,
    timeline_rows: list[dict[str, str]],
    original_audio: list[Path],
    cleaned_audio: list[Path],
    final_audio: Path | None,
) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "audition_status.json").write_text(
        json.dumps(asdict(status), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    plan = [
        "# Adobe Audition Editing Plan",
        "",
        "## Detection",
        f"- System: {status.system}",
        f"- Executable: {status.executable or 'Not found'}",
        f"- Can launch: {status.can_launch}",
        f"- Auto-import attempt: {status.can_auto_import}",
        "",
        "## Suggested Workflow",
        "1. Open podcast_edit.sesx in Adobe Audition for the editable arranged clips and music.",
        "2. Use audition_timeline.csv as the edit map for keep/delete/reorder decisions.",
        "3. Music is already placed in podcast_edit.sesx. For independent microphones, open source_multitrack.sesx when generated.",
        "4. Apply final voice cleanup conservatively: light noise reduction, de-esser, compressor, and loudness match.",
        "5. Use audio_qa_report.md for the configured target; remeasure loudness and peak after manual editing.",
        "6. SESX references are relative for relocation; keep the entire audition_package directory together.",
        "7. The editable session is pre-master, not the loudness-normalized final master. Re-export and remeasure after editing.",
        "8. Session XML/media are checked, but GUI loading and automated AU processing are not verified.",
        "9. Open ../review_package/index.html to compare bounded speech splice excerpts. Full listening is still required.",
        "",
        "## Files",
    ]
    for path in original_audio:
        plan.append(f"- Original: {path}")
    for path in cleaned_audio:
        plan.append(f"- Cleaned: {path}")
    if final_audio:
        plan.append(f"- Automated master: {final_audio}")
    if status.notes:
        plan.extend(["", "## Notes", *[f"- {note}" for note in status.notes]])

    (package_dir / "audition_edit_plan.md").write_text("\n".join(plan) + "\n", encoding="utf-8")

    script_path = package_dir / (
        "open_in_audition.ps1" if status.system in {"Windows", "win32"} else "open_in_audition.sh"
    )
    files_to_open = []
    session_file = package_dir / "podcast_edit.sesx"
    if session_file.exists():
        files_to_open.append(str(session_file))
    elif final_audio and final_audio.exists():
        files_to_open.append(str(final_audio))

    if status.system in {"Windows", "win32"}:
        exe = status.executable or ""

        def ps_literal(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        file_line = (
            "  $files = @((Join-Path $PSScriptRoot 'podcast_edit.sesx'))\n"
            if session_file.exists()
            else f"  $files = @({', '.join(ps_literal(item) for item in files_to_open)})\n"
        )
        content = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$audition = {ps_literal(exe)}\n"
            "if ($audition -and (Test-Path -LiteralPath $audition)) {\n"
            f"{file_line}"
            "  $quoted = ($files | ForEach-Object { '\"' + $_ + '\"' }) -join ' '\n"
            "  Start-Process -FilePath $audition -ArgumentList $quoted\n"
            "} else {\n"
            "  Write-Host 'Adobe Audition executable was not found. Open Audition manually and import the listed files.'\n"
            f"  Write-Host {ps_literal('Files: ' + ', '.join(files_to_open))}\n"
            "}\n"
        )
    else:
        app = status.executable or "Adobe Audition"
        if session_file.exists():
            open_lines = [
                'script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
                f'open -a {shlex.quote(app)} "$script_dir/podcast_edit.sesx"',
            ]
        else:
            open_lines = [f"open -a {shlex.quote(app)} {shlex.quote(item)}" for item in files_to_open] or [
                f"open -a {shlex.quote(app)}"
            ]
        content = "#!/usr/bin/env bash\nset -euo pipefail\n" + "\n".join(open_lines) + "\n"
    script_path.write_text(content, encoding="utf-8")


def write_markers_csv(path: Path, markers: list[dict]) -> None:
    # Reference CSV; WAV cue markers are the supported import handoff.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Name", "Start", "Duration", "Time Format", "Type", "Description"])
        writer.writeheader()
        for row in markers:
            writer.writerow(
                {
                    "Name": row["clip_id"],
                    "Start": f"{row['timeline_start']:.3f}",
                    "Duration": f"{row.get('duration', 0):.3f}",
                    "Time Format": "decimal",
                    "Type": "Cue",
                    "Description": row.get("text", "")[:180],
                }
            )


def launch_audition(status: AuditionStatus, files: list[Path]) -> tuple[bool, str]:
    if not status.executable:
        return False, "Adobe Audition executable was not found."
    try:
        if status.system == "Windows":
            subprocess.Popen([status.executable, *[str(path) for path in files if path.exists()]])
        elif status.system == "Darwin":
            subprocess.Popen(["open", "-a", status.executable, *[str(path) for path in files if path.exists()]])
        else:
            return False, f"Launching Audition is not implemented for {status.system}."
    except Exception as exc:
        return False, str(exc)
    return True, "Adobe Audition launch command was sent."


if __name__ == "__main__":
    status = detect_audition()
    print(json.dumps(asdict(status), ensure_ascii=False, indent=2))

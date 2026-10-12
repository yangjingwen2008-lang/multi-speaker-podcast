#!/usr/bin/env python3
"""Download low-risk intro/outro music candidates for interview podcasts."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class MusicTrack:
    provider: str
    title: str
    creator: str
    license: str
    license_url: str
    landing_url: str
    download_url: str
    local_path: str | None
    safe_for_auto_mix: bool
    style_score: int = 0
    selection_note: str = "Style not independently listening-verified."


def safe_name(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", text.strip())
    return text[:80] or "music"


def request_json(url: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "CodexPodcastSkill/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def is_safe_license(license_text: str, license_url: str) -> bool:
    return license_kind(license_text, license_url) == "public-domain"


def license_kind(license_text: str, license_url: str) -> str | None:
    """Recognize explicit licenses, never a substring in arbitrary metadata."""
    values = [license_url.strip(), license_text.strip()]
    kinds = []
    for value in values:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme in {"https", "http"}:
            if parsed.hostname not in {"creativecommons.org", "www.creativecommons.org"}:
                return None
            path = parsed.path.rstrip("/").lower()
            if re.fullmatch(r"/publicdomain/(zero|mark)/1\.0(?:/deed(?:\.[a-z-]+)?)?", path):
                kinds.append("public-domain")
            elif re.fullmatch(r"/licenses/by/(1\.0|2\.0|2\.5|3\.0|4\.0)(?:/deed(?:\.[a-z-]+)?)?", path):
                kinds.append("attribution")
            else:
                return None
        elif value:
            if re.fullmatch(r"(?:cc0(?: 1\.0)?|public domain|public domain mark(?: 1\.0)?)", value.lower()):
                kinds.append("public-domain")
            elif re.fullmatch(r"cc[- ]by (1\.0|2\.0|2\.5|3\.0|4\.0)", value.lower()):
                kinds.append("attribution")
            else:
                return None
    return kinds[0] if kinds and len(set(kinds)) == 1 else None


def metadata_text(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if len(value) == 1 else ""
    return str(value or "")


def build_keywords(podcast_name: str, episode_topic: str, music_style: str) -> str:
    pieces = [music_style, episode_topic, podcast_name, "instrumental intro outro calm interview podcast"]
    return " ".join(piece for piece in pieces if piece).strip()


def music_metadata_score(metadata: dict, keywords: str) -> int:
    fields = [metadata.get(key, "") for key in ("title", "subject", "description")]
    text = " ".join(" ".join(map(str, value)) if isinstance(value, list) else str(value) for value in fields).lower()
    text = re.sub(r"<[^>]+>", " ", text)
    words = set(re.findall(r"[a-z]+", text))
    if words & {"audiobook", "sermon", "speech", "spoken", "concert", "lyrics", "vocal", "vocals"}:
        return -1
    if not words & {"instrumental", "ambient", "background", "soundtrack"}:
        return -1
    generic = {"podcast", "interview", "intro", "outro", "music", "modern", "instrumental"}
    desired = set(re.findall(r"[a-z]+", keywords.lower())) - generic
    return 2 * len(words & desired) + len(words & {"instrumental", "ambient", "background", "calm", "soft", "minimal"})


def candidate_duration(value: Any) -> float | None:
    try:
        parts = str(value).split(":")
        if not 1 <= len(parts) <= 3:
            return None
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return total if 0 < total < 86400 else None
    except (ValueError, TypeError):
        return None


def internet_archive_candidates(keywords: str, limit: int = 10) -> list[MusicTrack]:
    terms = re.findall(r"[A-Za-z]+", keywords)[:8]
    search_terms = " OR ".join(f'"{term}"' for term in terms) or '"instrumental"'
    query = f"({search_terms}) AND mediatype:audio AND licenseurl:* AND (subject:instrumental OR subject:ambient OR subject:background)"
    params = {
        "q": query,
        "fl[]": ["identifier", "title", "creator", "licenseurl", "description", "subject"],
        "rows": str(min(50, max(10, limit * 5))),
        "page": "1",
        "output": "json",
        "sort[]": "downloads desc",
    }
    encoded = urllib.parse.urlencode(params, doseq=True)
    search_url = f"https://archive.org/advancedsearch.php?{encoded}"
    data = request_json(search_url)
    docs = data.get("response", {}).get("docs", [])
    docs.sort(key=lambda doc: music_metadata_score(doc, keywords), reverse=True)
    tracks: list[MusicTrack] = []

    for doc in docs:
        if len(tracks) >= limit:
            break
        if music_metadata_score(doc, keywords) < 0:
            continue
        identifier = doc.get("identifier")
        if not identifier:
            continue
        license_url = metadata_text(doc.get("licenseurl"))
        title = str(doc.get("title") or identifier)
        creator_value = doc.get("creator") or ""
        creator = ", ".join(creator_value) if isinstance(creator_value, list) else str(creator_value)
        metadata_url = f"https://archive.org/metadata/{urllib.parse.quote(identifier)}"
        try:
            metadata = request_json(metadata_url)
        except Exception:
            continue
        license_text = metadata_text(metadata.get("metadata", {}).get("licenseurl")) or license_url
        license_url = license_text
        if not license_kind(license_text, license_url):
            continue
        style_score = music_metadata_score(metadata.get("metadata", {}), keywords)
        if style_score < 0:
            continue
        files = metadata.get("files", [])
        chosen = None
        for item in files:
            name = item.get("name", "")
            lower = name.lower()
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                continue
            length = candidate_duration(item.get("length"))
            if (
                lower.endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a"))
                and "_64kb" not in lower
                and 0 < size <= 80 * 1024 * 1024
                and (length is None or 8 <= length <= 600)
            ):
                chosen = name
                break
        if not chosen:
            continue
        download_url = f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(chosen)}"
        landing_url = f"https://archive.org/details/{urllib.parse.quote(identifier)}"
        safe = is_safe_license(license_text, license_url)
        tracks.append(
            MusicTrack(
                provider="internet-archive",
                title=title,
                creator=creator,
                license=license_text or license_url,
                license_url=license_url,
                landing_url=landing_url,
                download_url=download_url,
                local_path=None,
                safe_for_auto_mix=safe,
                style_score=style_score,
                selection_note="Instrumental/ambient metadata match; long recordings and spoken/vocal tags excluded. Listening still required.",
            )
        )
    return sorted(tracks, key=lambda track: (track.safe_for_auto_mix, track.style_score), reverse=True)


def jamendo_candidates(keywords: str, client_id: str, limit: int = 10) -> list[MusicTrack]:
    params = {
        "client_id": client_id,
        "format": "json",
        "limit": str(limit),
        "search": keywords,
        "include": "musicinfo",
        "audioformat": "mp31",
        "vocalinstrumental": "instrumental",
        "order": "popularity_total",
    }
    url = f"https://api.jamendo.com/v3.0/tracks/?{urllib.parse.urlencode(params)}"
    data = request_json(url)
    if data.get("headers", {}).get("status") == "failed":
        raise ValueError("Jamendo API rejected the request; check client ID and provider report.")
    tracks: list[MusicTrack] = []
    for item in data.get("results", []):
        if item.get("audiodownload_allowed") is not True:
            continue
        license_ccurl = str(item.get("license_ccurl") or "")
        license_text = str(item.get("license") or license_ccurl)
        if not license_kind(license_text, license_ccurl):
            continue
        safe = is_safe_license(license_text, license_ccurl)
        tracks.append(
            MusicTrack(
                provider="jamendo",
                title=str(item.get("name") or item.get("id") or "Jamendo track"),
                creator=str(item.get("artist_name") or ""),
                license=license_text,
                license_url=license_ccurl,
                landing_url=str(item.get("shareurl") or ""),
                download_url=str(item.get("audiodownload") or item.get("audio") or ""),
                local_path=None,
                safe_for_auto_mix=safe,
            )
        )
    return tracks


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "CodexPodcastSkill/0.1"})
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, temporary.open("wb") as out:
            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise ValueError("Download is not an audio file.")
            count = 0
            while True:
                block = resp.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > 80 * 1024 * 1024:
                    raise ValueError("Music download exceeds 80 MB limit.")
                out.write(block)
            if count == 0:
                raise ValueError("Empty music download.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def make_stinger(ffmpeg: str, source: Path, output: Path, duration: float, target_db: float = -28.0) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode:
        return False
    actual = float(json.loads(probe.stdout)["format"]["duration"])
    duration = min(duration, actual)
    if duration <= 0:
        return False
    fade = min(2.0, duration / 3)
    fade_out = max(duration - fade, 0)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-af",
        f"loudnorm=I={target_db}:TP=-3:LRA=8,afade=t=in:st=0:d={fade:.3f},afade=t=out:st={fade_out:.3f}:d={fade:.3f}",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output),
    ]
    return subprocess.run(cmd, capture_output=True, timeout=600).returncode == 0


def fetch_music(
    output_dir: Path,
    podcast_name: str,
    episode_topic: str,
    music_style: str,
    provider: str = "internet-archive",
    jamendo_client_id: str | None = None,
    ffmpeg: str = "ffmpeg",
    allow_cc_attribution: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir = output_dir / "music_candidates"
    package_music_dir = output_dir / "audition_package" / "music"
    keywords = build_keywords(podcast_name, episode_topic, music_style)
    errors: list[str] = []

    try:
        if provider == "jamendo":
            if not jamendo_client_id:
                raise ValueError("Jamendo provider requires JAMENDO_CLIENT_ID or --jamendo-client-id.")
            candidates = jamendo_candidates(keywords, jamendo_client_id)
        else:
            candidates = internet_archive_candidates(keywords)
    except Exception as exc:
        candidates = []
        errors.append(str(exc))

    usable: list[MusicTrack] = []
    candidates.sort(key=lambda track: not track.safe_for_auto_mix)
    for index, track in enumerate(candidates[:5], start=1):
        if not track.download_url:
            continue
        kind = license_kind(track.license, track.license_url)
        if kind not in {"public-domain", "attribution"}:
            continue
        suffix = Path(urllib.parse.urlparse(track.download_url).path).suffix or ".mp3"
        local = candidates_dir / f"{index:02d}_{safe_name(track.title)}{suffix}"
        try:
            download_file(track.download_url, local)
        except Exception as exc:
            errors.append(f"Failed to download {track.title}: {exc}")
            continue
        track.local_path = str(local)
        if not track.safe_for_auto_mix and not allow_cc_attribution:
            continue
        intro = package_music_dir / "intro_music.wav"
        outro = package_music_dir / "outro_music.wav"
        try:
            if not (make_stinger(ffmpeg, local, intro, 12) and make_stinger(ffmpeg, local, outro, 20)):
                errors.append(f"Audio decode failed: {track.title}")
                continue
        except Exception as exc:
            errors.append(f"Audio processing failed: {track.title}: {exc}")
            continue
        usable.append(track)
        if len(usable) >= 1:
            break

    intro = package_music_dir / "intro_music.wav"
    outro = package_music_dir / "outro_music.wav"
    mixed = False
    if usable and usable[0].local_path:
        mixed = True

    if jamendo_client_id:
        errors = [message.replace(jamendo_client_id, "[REDACTED]") for message in errors]
    manifest = {
        "keywords": keywords,
        "provider": provider,
        "allow_cc_attribution": allow_cc_attribution,
        "auto_mix_ready": mixed,
        "errors": errors,
        "candidates": [asdict(item) for item in candidates],
        "selected": [asdict(item) for item in usable],
        "intro_music": str(intro) if intro.exists() else None,
        "outro_music": str(outro) if outro.exists() else None,
    }
    (output_dir / "music_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_license_report(output_dir / "music_license_report.md", manifest)
    write_candidates_csv(output_dir / "music_candidates.csv", candidates)
    return manifest


def provided_music(
    output_dir: Path, intro_source: str, outro_source: str, ffmpeg: str, license_text: str, creator: str = ""
) -> dict[str, Any]:
    if not (intro_source or outro_source):
        raise ValueError("Provided music needs --music-file, --intro-music, or --outro-music.")
    if not license_text.strip():
        raise ValueError("Record --music-license for provided music (license URL or your permission description).")
    selected, paths = [], {}
    for name, source, length in (("intro", intro_source, 12), ("outro", outro_source, 20)):
        if not source:
            paths[f"{name}_music"] = None
            continue
        path = Path(source).resolve()
        if not path.is_file():
            raise ValueError(f"Provided music missing: {path}")
        target = output_dir / "audition_package" / "music" / f"{name}_music.wav"
        if not make_stinger(ffmpeg, path, target, length):
            raise ValueError(f"Unable to decode provided music: {path}")
        paths[f"{name}_music"] = str(target)
        selected.append(
            {
                "title": path.name,
                "creator": creator,
                "license": license_text,
                "license_url": license_text if license_text.startswith("https://") else "",
                "landing_url": str(path),
                "local_path": str(path),
                "role": name,
            }
        )
    manifest = {
        "provider": "provided",
        "keywords": "",
        "auto_mix_ready": True,
        "selected": selected,
        "errors": [],
        "license_verification": "User-declared; not independently verified",
        **paths,
    }
    (output_dir / "music_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_license_report(output_dir / "music_license_report.md", manifest)
    return manifest


def write_candidates_csv(path: Path, tracks: list[MusicTrack]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "provider",
                "title",
                "creator",
                "license",
                "license_url",
                "landing_url",
                "safe_for_auto_mix",
                "download_url",
                "style_score",
                "selection_note",
            ],
        )
        writer.writeheader()
        for track in tracks:
            row = asdict(track)
            row.pop("local_path", None)
            writer.writerow(row)


def write_license_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# Music License Report",
        "",
        f"- Provider: {manifest['provider']}",
        f"- Search keywords: {manifest['keywords']}",
        f"- Auto mix ready: {manifest['auto_mix_ready']}",
        "",
        "## Policy",
        "- Commercial platform audio is never scraped.",
        "- Tracks without clear license metadata are not mixed into the final episode.",
        "- By default only Public Domain or CC0-style tracks are auto-mixed.",
        "",
        "## Selected",
    ]
    selected = manifest.get("selected", [])
    if manifest.get("license_verification"):
        lines.append(f"- Verification: {manifest['license_verification']}")
    if not selected:
        lines.append("- No track was selected for automatic mixing.")
    for track in selected:
        lines.extend(
            [
                f"- Title: {track.get('title')}",
                f"  Creator: {track.get('creator') or 'Unknown'}",
                f"  License: {track.get('license') or track.get('license_url') or 'Unknown'}",
                f"  Source: {track.get('landing_url')}",
            ]
        )
    if manifest.get("errors"):
        lines.extend(["", "## Errors", *[f"- {error}" for error in manifest["errors"]]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch low-risk podcast intro/outro music.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--podcast-name", default="")
    parser.add_argument("--episode-topic", default="")
    parser.add_argument("--music-style", default="calm modern instrumental interview podcast")
    parser.add_argument("--provider", choices=["internet-archive", "jamendo"], default="internet-archive")
    parser.add_argument("--jamendo-client-id", default=os.environ.get("JAMENDO_CLIENT_ID"))
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--allow-cc-attribution", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = fetch_music(
        Path(args.output_dir),
        args.podcast_name,
        args.episode_topic,
        args.music_style,
        args.provider,
        args.jamendo_client_id,
        args.ffmpeg,
        args.allow_cc_attribution,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest.get("auto_mix_ready") else 2


if __name__ == "__main__":
    sys.exit(main())

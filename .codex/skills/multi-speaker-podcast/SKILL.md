---
name: multi-speaker-podcast
description: End-to-end workflow for multi-speaker interview podcasts. Use when Codex needs to process one or more raw podcast interview audio files (.mp3, .wav, .m4a, .flac, .ogg, .aac) into a publishable episode with audio cleanup, speaker diarization when available, transcription, content scoring, edit decisions, timeline packaging, intro/outro music with license checks, final loudness export, and Adobe Audition handoff packages.
---

# Multi-Speaker Podcast

## Quick Start

Use `scripts/podcast_pipeline.py` for the full MVP pipeline:

```powershell
python .codex/skills/multi-speaker-podcast/scripts/podcast_pipeline.py `
  --input raw1.wav raw2.mp3 `
  --output-dir podcast_output `
  --podcast-name "Podcast Name" `
  --episode-topic "Episode Topic" `
  --host "Host Name" `
  --guest "Guest Name" `
  --target-format mp3 `
  --target-lufs -16 `
  --install-ai-deps `
  --music-mode auto `
  --music-style "calm modern instrumental interview podcast" `
  --enable-audition
```

The script requires Python 3.10+, `ffmpeg` and `ffprobe`; auto installation also requires `uv`. Use `--skip-ai` for audio-only execution. Use `--config` for a JSON config (CLI overrides config; config-relative paths resolve from its directory). See `references/configuration.md` for config, transcript and edit-plan schemas.

## Workflow

1. Validate input audio paths and probe format, duration, sample rate, channels, and bitrate.
2. Convert all inputs to 48 kHz stereo WAV work files.
3. Apply conservative, time-preserving cleanup: light denoise, high/low pass, compression and loudness normalization. Never remove pauses before ASR or diarization; source timestamps must remain valid.
   For simultaneously recorded microphones use `--input-mode multitrack` with explicit `track_offsets`, `track_gains` and `track_names` JSON objects. Do not infer acoustic synchronization. ASR/edit coordinates then refer to the shared `multitrack_mix.wav`, not an individual microphone.
4. If enabled, install or reuse the managed AI environment and run `faster-whisper`.
5. If `pyannote.audio` and a Hugging Face token are available, run speaker diarization with `pyannote/speaker-diarization-community-1`.
6. Generate raw transcript, scores and an initial source-time plan. Read the actual transcript before semantic editing; scores are heuristic review cues. Preserve missing-transcript sources in full. Speaker numbers are independent across files; use manual mappings rather than guessing identities.
7. Optionally fetch intro/outro music only from allowed sources with license metadata.
8. Default render keeps all conversation, shortening only measured long silences with 0.5s context padding. For editorial cleanup, generate a `source_seconds` edit-plan JSON from the real transcript, preserving question/answer context and original meaning. Rerun with `--edit-plan` and `--transcript-json` into a new output directory. Plan clip order is output order. Do not assume ASR sentence bounds alone are safe cuts: include context and review transitions. Distinguish proposed highlights from actual reorder decisions.
9. Export a 48kHz/24-bit WAV master with cue markers plus MP3/WAV release. Check encoded release loudness, true peak and full decode through `audio_qa_report.md`; technical checks do not constitute a full listening review.
10. Generate `audition_package/` for Adobe Audition review and finishing. Explicitly report limitations when transcription, music fetching or AU automation is unavailable.
11. Inspect `production_status.json`, final-timeline SRT/VTT and partial-utterance warnings. Open `review_package/index.html` for bounded splice checks, then listen to the full episode before claiming publication readiness. Use `--require-technical-qa` to return exit code 2 on failed technical QA.
12. Run `scripts/validate_output.py <output-directory>` for independent artifact/hash/timeline/media acceptance. A passing structural audit does not verify ASR accuracy, editorial meaning, music ownership, AU GUI or listening quality. Do not run acceptance with Python `-O`.

## Outputs

The default output directory contains:

- `final_podcast.mp3` or `final_podcast.wav`
- `master_podcast.wav` with source-derived clip cue markers
- `transcript.json`, `content_scores.json`, `edit_decision_list.json`
- `audio_qa.json`, `audio_qa_report.md`
- `run_config.json` (credentials omitted), `release_manifest.json` (export SHA256)
- `transcript_raw.md`
- `transcript_cleaned.md`
- `podcast_structure.md`
- `edit_decision_list.md`
- `edit_report.md`
- `show_notes.md`
- `audio_info_report.md`
- `music_manifest.json`
- `music_license_report.md`
- `audition_timeline.csv`
- `audition_package/`
- `final_subtitles.srt`, `final_subtitles.vtt`, `subtitle_report.json`
- `review_package/index.html`, listening excerpts and `review_manifest.json`
- `production_status.json`, optional `multitrack_report.json`
- `run_state.json` with run ID and running/failed/completed/technical_qa_failed status; optional `run_history/` with previous status manifests, not full audio backups

`audition_package/` contains original audio copies, cleaned audio, generated clips, music stingers when available, timeline CSV files, an edit plan, an AU status manifest, and a launch script.

## AI Dependencies

Use `--install-ai-deps` to create a managed environment at `%LOCALAPPDATA%\CodexPodcastSkill\venvs\podcast-ai`.

The installer uses `uv`, creates Python 3.11, detects NVIDIA/CUDA availability, tries CUDA PyTorch first, falls back to CPU PyTorch, and validates library imports. PyTorch/torchaudio 2.8.0, torchcodec 0.7.x and matplotlib 3.10.x are constrained as a compatible runtime; heavyweight libraries are installed outside the repository:

- `torch`
- `torchaudio`
- `faster-whisper`
- `pyannote.audio`

For speaker diarization, prefer `HUGGINGFACE_TOKEN` over `--hf-token` to avoid shell-history exposure. The user must have accepted the model terms. If diarization fails, use `Unknown`, not a false single-speaker assertion. The bridge supports the community-1 output object and exclusive diarization. GPU transcription retries on CPU if CUDA inference fails; missing libraries/model access degrade to preserved audio.

## Music

Use `--music-mode auto` only when internet access is acceptable. The music fetcher never scrapes commercial music platforms. It uses:

- Internet Archive by default.
- Jamendo only when `JAMENDO_CLIENT_ID` is provided.

By default only recognized CC0/Public Domain tracks are mixed automatically. Recognized CC BY tracks may be downloaded as candidates; `--allow-cc-attribution-music` allows mixing with show-note credits. It never bypasses NC/ND/unknown license restrictions. Source metadata is a declaration, not independently verified ownership. Read `references/music_licensing.md` before changing this policy.

Use `--music-mode provided --music-file <path> --music-license <license or permission>` for local music. Separate `--intro-music` and `--outro-music` are supported. No continuous bed or generated music is currently implemented. `--generate-intro-outro` writes copy for recording; it does not synthesize voices.

## Adobe Audition

Use `--enable-audition` to detect Adobe Audition. Use `--audition-path` when the executable is not found automatically. The bridge checks running processes, Windows registry App Paths, then common install paths.

Use `--open-audition` to send a file-open request for the marked WAV master; `--attempt-au-import` dispatches `podcast_edit.sesx`. The generated editable session arranges clips/music using relative media paths; multitrack mode also exports `source_multitrack.sesx`. Sessions use a locally inspected AU 23 schema with XML/media/reconstruction tests, not GUI-verified compatibility. They are pre-master sessions; remeasure after AU export. No dispatch proves import or editing completion. Read `references/audition_workflow.md` before extending integration.

Default inputs are sequential recording parts; explicit multitrack mode aligns microphones using supplied nonnegative offsets. Diarization does not separate overlapping voices or remove bleed. Reusable transcripts/EDLs carry source fingerprints and reject changed recordings, input order or alignment/gains. Do not remove this guard to force an incompatible plan.

Output directories and managed AI setup use kernel locks. A concurrent run rejects instead of modifying the lock holder's files. Do not delete a lock file to bypass a running process; the kernel releases it after process exit. `--overwrite` invalidates old success manifests and records a fresh run ID, but does not delete unrelated files or implement resume. Windows atomic journal writes have bounded retries; persistent permission errors remain failures.

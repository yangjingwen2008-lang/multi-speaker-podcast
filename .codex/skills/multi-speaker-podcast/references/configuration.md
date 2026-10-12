# Configuration and Editorial Plans

Config keys match CLI names with underscores, for example `target_lufs`, `input`, `guest`, `music_mode`. Use JSON booleans, not strings. Reject unknown keys. Relative paths resolve from the config directory; CLI paths resolve from the working directory. CLI boolean switches support `--no-...` for installation, Audition detection, silence trim and attribution music. Credentials belong in environment variables, not config.

```json
{
  "input": ["raw/part1.wav", "raw/part2.m4a"],
  "output_dir": "outputs/episode01",
  "podcast_name": "Interview",
  "episode_topic": "Episode theme",
  "target_lufs": -16,
  "install_ai_deps": true,
  "music_mode": "auto",
  "speaker_map": {"part1.wav:Speaker 1": "Host"}
}
```

The reusable transcript format is `{"segments": [...]}`; every segment has `source_file`, `start`, `end`, `speaker`, `text`. Times are seconds from the original source, unchanged by preprocessing. `source_file` can be an absolute path, a unique input basename, or a path relative to the JSON file. Never fabricate missing text. `transcript.json` is a reusable output; per-source word timestamps remain under `work/analysis/`.

The edit plan passed via `--edit-plan` uses `clips` in the desired final order. Its coordinates are always original source seconds. Retain safe boundaries around full utterances and relevant responses; ASR timestamps can be imperfect. Scores alone are not authority to remove core viewpoints, disagreement, emotion or useful short interjections.

```json
{
  "coordinate_system": "source_seconds",
  "clips": [
    {"source_file": "part1.wav", "start": 30, "end": 50, "reason": "Complete hook with context"},
    {"source_file": "part1.wav", "start": 0, "end": 30, "reason": "Original introduction"},
    {"source_file": "part2.m4a", "start": 2, "end": 60, "reason": "Continue the answer"}
  ]
}
```

The output `edit_decision_list.json` includes clip IDs, actual render source ranges, final timeline positions, and a complement of all omitted source ranges. Reordered/duplicated clips are allowed when explicitly present in the plan. A plan with zero clips, invalid bounds, NaN/infinity, missing sources or ambiguous basenames fails before rendering. A manual plan overrides automatic silence trimming.

Set `keep: false` on an edit clip to omit it, or remove it from the array. At least one retained clip is required. Start/end must be JSON numbers, not booleans or numeric strings.

## Multitrack Mode

Use `input_mode: "multitrack"` for simultaneous microphones, never for sequential recording parts. `track_offsets`, `track_gains` and `track_names` are objects keyed by one-based input index strings, unique basenames or full input paths. Index keys avoid duplicate-basename ambiguity. Offsets are nonnegative seconds (positive means later start), gains are -36..18 dB, names are nonempty labels. Unspecified offsets/gains are zero. Track labels do not establish speaker identity.

The pipeline independently cleans microphones, applies gains, delays them on a 48kHz sample clock and sums a float mix. No acoustic auto-alignment, bleed removal or overlapping-voice separation is inferred. The transcript/EDL source is `multitrack_mix.wav`; times are shared-mix seconds. Keep identical inputs, order, offsets and gains when rerendering a plan. Generated `source_identity` fingerprints reject changed source content or alignment; do not delete the fingerprint to bypass validation. Manual external JSON without a fingerprint is accepted as an explicit user-supplied plan.

## Review Outputs

Final SRT/VTT only include complete transcript utterances inside retained clips. Partial sentences are omitted and listed in `subtitle_report.json`, not fabricated. `review_limit` bounds generated speech splice comparisons (default 20; 0..100); `review_package/index.html` is a standalone local report. `production_status.json` never substitutes technical measurements for full listening/editorial review. `require_technical_qa: true` returns exit code 2 when loudness/peak checks fail, with artifacts retained.

Use `--transcript-json first_run/transcript.json --edit-plan reviewed_plan.json --output-dir second_run` to render editorial decisions without rerunning AI. This does not eliminate the need to listen to the resulting transitions. The pipeline does not auto-publish, provide TTS narration, or claim an AI understanding of topics from heuristic scores.

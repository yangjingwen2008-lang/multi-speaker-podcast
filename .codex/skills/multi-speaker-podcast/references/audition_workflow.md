# Adobe Audition Workflow Reference

Use Adobe Audition as a finishing workstation, not as the only source of truth. The automated pipeline should always preserve auditable files: original audio, cleaned audio, clips, timeline CSV, edit plan, and manifests.

## Detection

- Prefer an explicit `--audition-path` when supplied.
- On Windows, inspect running `Adobe Audition.exe` processes first, then registry App Paths (both 32-bit/64-bit views), then common Adobe install folders. Registered non-standard install locations work even when AU is closed.
- On macOS, scan `/Applications/Adobe Audition*/`.
- If detection fails, still generate `audition_package/` and explain manual import steps.

## Package Contents

- `original_audio/`: exact user audio copies.
- `cleaned_audio/`: normalized WAV files after conservative cleanup.
- `clips/`: automatically cut kept segments.
- `music/`: intro/outro WAV stingers when license-safe music was selected.
- `audition_timeline.csv`: edit map for keep/delete/reorder work.
- `audition_markers.csv`: final timeline marker reference (Name, Start, Duration, Time Format, Type, Description). Do not claim arbitrary CSV can be imported by AU.
- `../master_podcast.wav`: standard RIFF cue/LIST markers embedded directly in the master. Open the master in Waveform view and inspect the Markers panel; marker display varies by version. CSV remains the fallback reference for oversized/RF64 WAV files.
- `audition_edit_plan.md`: human workflow for AU.
- `audition_manifest.json`: machine-readable package manifest.
- `open_in_audition.ps1` or `.sh`: best-effort launch script.
- `podcast_edit.sesx`: editable arranged clips and music, with relative media references.
- `source_multitrack.sesx`: independent cleaned microphone tracks with explicit delay/gain, multitrack mode only.
- `session_report.json`: session details and verification limitations.

## Operating Rule

Never claim that AU performed edits unless a command actually launched or a user-provided AU automation script ran. If the bridge only creates a package, report it as a semi-automatic AU workflow.

Sending a launch command only confirms dispatch, not successful import or edit completion. SESX generation follows a locally inspected AU 23 session schema (48kHz sample-based startPoint/endPoint and sourceInPoint/sourceOutPoint). XML, file references and microphone-session reconstruction are tested; GUI compatibility is not confirmed. Preserve the entire package on relocation. The source session exposes pre-master gain/delay, while the edit session contains the rendered clips and music arrangement; neither reproduces final mastering as an AU effect rack. Use the QA target after editing, re-export and remeasure. No verified command-line AU editing API is assumed.

Marker reference: https://helpx.adobe.com/audition/desktop/editing-audio-files/markers.html

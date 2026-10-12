from __future__ import annotations

import contextlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / ".codex/skills/multi-speaker-podcast/scripts"
sys.path.insert(0, str(SCRIPTS))
import audio_editing
import audition_bridge
import audition_session
import multitrack
import music_fetcher
import podcast_pipeline as pipeline
import review_package
import setup_ai_env


class TimingAndPolicyTests(unittest.TestCase):
    def test_track_settings_reject_ambiguity_and_invalid_values(self):
        assets = [
            pipeline.AudioAsset(Path(f"{folder}/same.wav"), Path("x"), Path("x"), Path("x"), {})
            for folder in ("one", "two")
        ]
        for offsets, gains in (
            ({"same.wav": 0}, {}),
            ({"3": 0}, {}),
            ({"1": -1}, {}),
            ({"1": float("nan")}, {}),
            ({}, {"1": True}),
            ({}, {"1": 19}),
        ):
            with self.assertRaises(ValueError):
                multitrack.track_settings(assets, offsets, gains, {})
        rows = multitrack.track_settings(assets, {"2": 0.25}, {"1": -3}, {"1": "Host microphone"})
        self.assertEqual(rows[1]["offset"], 0.25)
        self.assertEqual(rows[0]["gain_db"], -3)

    def test_subtitles_skip_partial_words_and_follow_reorder(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            segments = [
                pipeline.TranscriptSegment("a.wav", 1, 3, "Host", "first"),
                pipeline.TranscriptSegment("a.wav", 4, 5, "Guest", "second"),
                pipeline.TranscriptSegment("a.wav", 5, 7, "Guest", "partial"),
            ]
            plan = {
                "clips": [
                    {
                        "clip_id": "c1",
                        "source_file": "a.wav",
                        "start": 4,
                        "end": 6,
                        "timeline_start": 10,
                        "timeline_end": 12,
                    },
                    {
                        "clip_id": "c2",
                        "source_file": "a.wav",
                        "start": 1,
                        "end": 3,
                        "timeline_start": 12,
                        "timeline_end": 14,
                    },
                ]
            }
            report = review_package.write_subtitles(output, plan, segments)
            text = (output / "final_subtitles.srt").read_text(encoding="utf-8")
            self.assertEqual(report["cues"], 2)
            self.assertEqual(len(report["partial_utterances_omitted"]), 1)
            self.assertLess(text.index("second"), text.index("first"))
            self.assertIn("00:00:10,000 --> 00:00:11,000", text)
            self.assertNotIn("partial", text)

    def test_multitrack_options_do_not_silently_affect_sequence(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pipeline.parse_args(["--input", "a.wav", "--track-gains", '{"1": -3}'])

    def test_transcript_rejects_final_coordinates_and_bad_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "transcript.json"
            for data in ([], {"coordinate_system": "final_seconds", "segments": []}, {"segments": [False]}):
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    pipeline.load_transcript(path, [], {})

    def test_pause_interiors_only(self):
        self.assertEqual(audio_editing.keep_ranges(10, [(3, 7)]), [(0, 3.5), (6.5, 10)])
        self.assertEqual(audio_editing.keep_ranges(1, []), [(0, 1)])

    def test_music_metadata_is_not_a_style_guarantee(self):
        self.assertGreater(music_fetcher.music_metadata_score({"subject": ["calm", "instrumental"]}, "calm tech"), 0)
        self.assertEqual(music_fetcher.music_metadata_score({"subject": "music podcast speech"}, "calm"), -1)
        self.assertEqual(music_fetcher.music_metadata_score({"title": "popular music"}, "calm"), -1)
        self.assertEqual(music_fetcher.candidate_duration("40:25"), 2425)
        self.assertIsNone(music_fetcher.candidate_duration("bad"))

    def test_archive_candidate_uses_short_licensed_instrumental_media(self):
        license_url = "https://creativecommons.org/publicdomain/zero/1.0/"
        search = {
            "response": {
                "docs": [
                    {
                        "identifier": "calm-test",
                        "title": "Calm instrumental",
                        "subject": "ambient instrumental",
                        "licenseurl": license_url,
                    }
                ]
            }
        }
        metadata = {
            "metadata": {"subject": "calm ambient instrumental", "licenseurl": license_url},
            "files": [
                {"name": "long.mp3", "size": "1234", "length": "40:25"},
                {"name": "short.mp3", "size": "1234", "length": "2:12"},
            ],
        }
        with patch.object(music_fetcher, "request_json", side_effect=[search, metadata]):
            tracks = music_fetcher.internet_archive_candidates("calm instrumental", limit=1)
        self.assertEqual(len(tracks), 1)
        self.assertTrue(tracks[0].download_url.endswith("short.mp3"))
        self.assertTrue(tracks[0].safe_for_auto_mix)
        self.assertGreater(tracks[0].style_score, 0)

    def test_overlapping_pauses_do_not_duplicate_audio(self):
        self.assertEqual(audio_editing.keep_ranges(10, [(2, 7), (3, 8)]), [(0, 2.5), (7.5, 10)])

    def test_license_policy_rejects_restricted_and_missing(self):
        for license_value in (
            "",
            "not public domain",
            "https://creativecommons.org/licenses/by-nc/4.0/",
            "https://creativecommons.org/licenses/by-nd/4.0/",
            "https://evil.example/creativecommons.org/publicdomain/zero/1.0/",
        ):
            self.assertIsNone(music_fetcher.license_kind(license_value, license_value))
        self.assertFalse(music_fetcher.is_safe_license("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"))
        self.assertEqual(
            music_fetcher.license_kind("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"), "attribution"
        )
        self.assertTrue(music_fetcher.is_safe_license("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"))
        self.assertIsNone(music_fetcher.license_kind("CC0", "https://creativecommons.org/licenses/by-nc/4.0/"))

    def test_chinese_meaningful_connectors_are_preserved(self):
        self.assertEqual(
            pipeline.clean_text("这个问题就是重要，然后我们讨论那个案例。"), "这个问题就是重要，然后我们讨论那个案例。"
        )
        self.assertEqual(pipeline.clean_text("嗯，建议保留。"), "建议保留。")

    def test_chinese_density_and_short_speech(self):
        self.assertGreater(pipeline.score_segment("关键是保留上下文，因为这段是重要经验和故事。", 8)[1], 1)
        self.assertTrue(pipeline.score_segment("不对。", 0.8)[3])
        self.assertTrue(pipeline.score_segment("", 0.5)[3])

    def test_failed_import_validation_is_not_success(self):
        self.assertFalse(setup_ai_env.validation_ok({"validation_error": "DLL failed"}))
        self.assertFalse(setup_ai_env.validation_ok({}))

    def test_gpu_subprocess_failure_retries_cpu(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "transcript.json"

            def fake_run(command, **kwargs):
                device = command[-2]
                if device == "cpu":
                    output.write_text(json.dumps({"device": "cpu", "segments": []}), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0 if device == "cpu" else 1, "", "GPU failure")

            with (
                patch.object(pipeline, "python_has_module", return_value=True),
                patch.object(pipeline.subprocess, "run", side_effect=fake_run) as execute,
            ):
                ok, message = pipeline.transcribe_with_faster_whisper(
                    Path(sys.executable), Path("audio.wav"), "small", "auto", output
                )
            self.assertTrue(ok)
            self.assertEqual(execute.call_count, 2)
            self.assertIn("CPU retry", message)

    def test_attribution_opt_in_is_not_a_license_bypass(self):
        with tempfile.TemporaryDirectory() as folder:
            track = music_fetcher.MusicTrack(
                "internet-archive",
                "Restricted",
                "Author",
                "",
                "https://creativecommons.org/licenses/by-nc/4.0/",
                "https://archive.org/details/x",
                "https://archive.org/download/x/a.mp3",
                None,
                False,
            )
            with (
                patch.object(music_fetcher, "internet_archive_candidates", return_value=[track]),
                patch.object(music_fetcher, "download_file") as download,
            ):
                manifest = music_fetcher.fetch_music(Path(folder), "", "", "ambient", allow_cc_attribution=True)
            download.assert_not_called()
            self.assertFalse(manifest["auto_mix_ready"])

    def test_by_music_is_candidate_only_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            track = music_fetcher.MusicTrack(
                "internet-archive",
                "Music",
                "Author",
                "CC BY 4.0",
                "https://creativecommons.org/licenses/by/4.0/",
                "https://archive.org/details/x",
                "https://archive.org/download/x/a.mp3",
                None,
                False,
            )

            def fake_download(url, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"candidate")

            with (
                patch.object(music_fetcher, "internet_archive_candidates", return_value=[track]),
                patch.object(music_fetcher, "download_file", side_effect=fake_download),
                patch.object(music_fetcher, "make_stinger") as stinger,
            ):
                manifest = music_fetcher.fetch_music(Path(folder), "", "", "ambient")
            stinger.assert_not_called()
            self.assertFalse(manifest["auto_mix_ready"])
            self.assertTrue(Path(manifest["candidates"][0]["local_path"]).is_file())

    def test_config_paths_overrides_and_boolean_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "input": ["raw.wav"],
                        "output_dir": "out",
                        "target_lufs": -18,
                        "guest": ["old"],
                        "install_ai_deps": True,
                    }
                ),
                encoding="utf-8",
            )
            args = pipeline.parse_args(
                ["--config", str(config), "--target-lufs", "-16", "--guest", "new", "--no-install-ai-deps"]
            )
            self.assertEqual(args.target_lufs, -16)
            self.assertEqual(args.guest, ["new"])
            self.assertFalse(args.install_ai_deps)
            self.assertEqual(args.input, [str(Path(folder) / "raw.wav")])
            config.write_text(json.dumps({"input": ["x.wav"], "trim_silence": "false"}))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pipeline.parse_args(["--config", str(config)])

    def test_manual_ranges_reject_nan_and_out_of_bounds(self):
        asset = pipeline.AudioAsset(
            Path("a.wav"), Path("copy.wav"), Path("work.wav"), Path("clean.wav"), {"format": {"duration": "10"}}
        )
        for row in (
            {"start": -1, "end": 1},
            {"start": 1, "end": 11},
            {"start": float("nan"), "end": 2},
            {"start": 10.01, "end": 10.03},
        ):
            with self.assertRaises(ValueError):
                pipeline.validated_range(row, asset)

    def test_au_launcher_quotes_apostrophes_and_dollar_signs(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            master = base / "host's $episode.wav"
            master.touch()
            status = audition_bridge.AuditionStatus(
                "Windows", "C:\\AU's $folder\\Audition.exe", "explicit_path", True, True, []
            )
            audition_bridge.write_audition_package(base / "package", status, [], [], [], master)
            text = (base / "package/open_in_audition.ps1").read_text(encoding="utf-8")
            self.assertIn("AU''s $folder", text)
            self.assertIn("host''s $episode.wav", text)
            if shutil.which("powershell"):
                cmd = [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "$tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors); if ($errors.Count) { $errors; exit 1 }",
                    str(base / "package/open_in_audition.ps1"),
                ]
                # Parse through an environment path rather than positional PowerShell -Command args.
                cmd[-2] = (
                    "$tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('"
                    + str(base / "package/open_in_audition.ps1").replace("'", "''")
                    + "',[ref]$tokens,[ref]$errors); if ($errors.Count) { $errors; exit 1 }"
                )
                cmd.pop()
                self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)

    def test_au_registry_detection_when_closed(self):
        with (
            patch.object(audition_bridge.platform, "system", return_value="Windows"),
            patch.object(audition_bridge, "_process_paths_windows", return_value=[]),
            patch.object(audition_bridge, "_registered_paths_windows", return_value=[Path("D:/AU/Adobe Audition.exe")]),
        ):
            status = audition_bridge.detect_audition()
        self.assertEqual(status.detected_from, "registry_app_paths")
        self.assertTrue(status.can_launch)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="podcast test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def tone(self, path: Path, seconds: float = 4):
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path)],
            check=True,
        )

    def invoke(self, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "podcast_pipeline.py"), *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_partial_transcript_duplicate_names_and_music_timeline(self):
        first = self.root / "one" / "host's interview.wav"
        second = self.root / "two" / first.name
        self.tone(first, 4)
        self.tone(second, 5)
        transcript = self.root / "transcript.json"
        transcript.write_text(
            json.dumps(
                {
                    "segments": [
                        {"source_file": str(first), "start": 0, "end": 2, "speaker": "Speaker 1", "text": "关键观点。"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "result"
        self.invoke(
            "--input",
            first,
            second,
            "--output-dir",
            output,
            "--transcript-json",
            transcript,
            "--no-trim-silence",
            "--music-mode",
            "provided",
            "--music-file",
            second,
            "--music-license",
            "Original owned music",
        )
        plan = json.loads((output / "edit_decision_list.json").read_text(encoding="utf-8"))
        self.assertEqual(len(plan["clips"]), 2)
        self.assertEqual([c["duration"] for c in plan["clips"]], [4, 5])
        self.assertAlmostEqual(plan["clips"][0]["timeline_start"], 5, places=2)
        self.assertEqual(len(list((output / "audition_package/original_audio").iterdir())), 2)
        self.assertIn(second.name, (output / "transcript_raw.md").read_text(encoding="utf-8"))
        self.assertIn("00:00:05.000", (output / "show_notes.md").read_text(encoding="utf-8"))
        qa = json.loads((output / "audio_qa.json").read_text(encoding="utf-8"))
        self.assertTrue(qa["decode_ok"])
        self.assertTrue(qa["loudness_within_1_lu"])
        self.assertTrue(qa["peak_below_minus_1_dbtp"])
        wav = (output / "master_podcast.wav").read_bytes()
        self.assertIn(b"cue ", wav)
        self.assertIn(b"adtl", wav)
        self.assertEqual(struct.unpack("<I", wav[4:8])[0], len(wav) - 8)

    def test_manual_reorder_and_clip_duration(self):
        source = self.root / "source.wav"
        self.tone(source, 7)
        plan = self.root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "clips": [
                        {"source_file": "source.wav", "start": 4, "end": 6},
                        {"source_file": "source.wav", "start": 1, "end": 3},
                    ]
                }
            )
        )
        output = self.root / "result"
        self.invoke(
            "--input", source, "--output-dir", output, "--skip-ai", "--edit-plan", plan, "--target-format", "wav"
        )
        actual = json.loads((output / "edit_decision_list.json").read_text(encoding="utf-8"))
        self.assertEqual([c["start"] for c in actual["clips"]], [4, 1])
        self.assertAlmostEqual(pipeline.duration("ffprobe", output / "final_podcast.wav"), 4, places=2)
        self.assertEqual([(d["start"], d["end"]) for d in actual["deleted_ranges"]], [(0, 1), (3, 4), (6, 7)])
        review = json.loads((output / "review_package/review_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(review["generated"], 1)
        self.assertFalse(review["listening_verified"])
        self.assertAlmostEqual(
            pipeline.duration("ffprobe", output / "review_package/splice_001_edited.wav"), 4, places=2
        )
        session = ET.parse(output / "audition_package/podcast_edit.sesx")
        clips = session.findall("./session/tracks/audioTrack/audioClip")
        self.assertEqual([int(c.get("startPoint")) for c in clips], [0, 96000])
        for file in session.findall("./files/file"):
            self.assertTrue((output / "audition_package" / file.get("relativePath")).is_file())

    def test_multitrack_shared_clock_and_independent_session(self):
        first, second = self.root / "host.wav", self.root / "guest.wav"
        self.tone(first, 3)
        self.tone(second, 4)
        output = self.root / "multitrack"
        self.invoke(
            "--input",
            first,
            second,
            "--input-mode",
            "multitrack",
            "--track-offsets",
            '{"2":1}',
            "--track-gains",
            '{"1":-6}',
            "--track-names",
            '{"1":"Host","2":"Guest"}',
            "--output-dir",
            output,
            "--skip-ai",
            "--no-trim-silence",
        )
        self.assertAlmostEqual(pipeline.duration("ffprobe", output / "master_podcast.wav"), 5, places=2)
        report = json.loads((output / "multitrack_report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["automatic_acoustic_sync"])
        source = ET.parse(output / "audition_package/source_multitrack.sesx")
        tracks = source.findall("./session/tracks/audioTrack")
        self.assertEqual([t.findtext("trackParameters/name") for t in tracks], ["Host", "Guest"])
        self.assertEqual(int(tracks[1].find("audioClip").get("startPoint")), 48000)
        gain = tracks[0].find("trackAudioParameters/component/parameter").get("parameterValue")
        self.assertAlmostEqual(float(gain), 10 ** (-6 / 20))
        # Reconstruct the exported source session with FFmpeg and compare to the float mix.
        settings = []
        files = {
            f.get("id"): output / "audition_package" / f.get("relativePath") for f in source.findall("./files/file")
        }
        for track in tracks:
            clip = track.find("audioClip")
            volume = float(track.find("trackAudioParameters/component/parameter").get("parameterValue"))
            import math

            settings.append(
                {
                    "cleaned_file": str(files[clip.get("fileID")]),
                    "offset": int(clip.get("startPoint")) / audition_session.RATE,
                    "gain_db": 20 * math.log10(volume),
                }
            )
        rebuilt = self.root / "rebuilt.wav"
        multitrack.mix_tracks("ffmpeg", settings, rebuilt, self.root / "rebuilt.json")
        self.assertEqual(rebuilt.read_bytes(), (output / "work/multitrack_float.wav").read_bytes())
        self.invoke(
            "--input",
            first,
            second,
            "--input-mode",
            "multitrack",
            "--track-offsets",
            '{"2":1}',
            "--track-gains",
            '{"1":-6}',
            "--output-dir",
            self.root / "rerun",
            "--skip-ai",
            "--transcript-json",
            output / "transcript.json",
            "--edit-plan",
            output / "edit_decision_list.json",
        )
        failed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "podcast_pipeline.py"),
                "--input",
                str(first),
                str(second),
                "--input-mode",
                "multitrack",
                "--track-offsets",
                '{"2":0.5}',
                "--output-dir",
                str(self.root / "mismatched"),
                "--skip-ai",
                "--edit-plan",
                str(output / "edit_decision_list.json"),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("different audio", failed.stderr)

    def test_strict_qa_retains_artifacts_and_fails_silent_release(self):
        source = self.root / "silent.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", str(source)],
            check=True,
        )
        output = self.root / "silent_result"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "podcast_pipeline.py"),
                "--input",
                str(source),
                "--output-dir",
                str(output),
                "--skip-ai",
                "--no-trim-silence",
                "--require-technical-qa",
            ],
            capture_output=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 2)
        self.assertTrue((output / "final_podcast.mp3").is_file())
        status = json.loads((output / "production_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "technical_qa_failed")

    def test_silence_trim_preserves_source_coordinates(self):
        source = self.root / "pause.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "aevalsrc=if(between(t\\,2\\,5)\\,0\\,0.1*sin(2*PI*440*t)):s=48000:d=7",
                str(source),
            ],
            check=True,
        )
        output = self.root / "result"
        self.invoke("--input", source, "--output-dir", output, "--skip-ai")
        actual = json.loads((output / "edit_decision_list.json").read_text(encoding="utf-8"))
        self.assertEqual(len(actual["clips"]), 2)
        self.assertAlmostEqual(actual["clips"][0]["end"], 2.5, delta=0.03)
        self.assertAlmostEqual(actual["clips"][1]["start"], 4.5, delta=0.03)
        self.assertAlmostEqual(
            pipeline.duration("ffprobe", output / "audition_package/cleaned_audio/01_pause_cleaned.wav"), 7, places=2
        )
        self.assertAlmostEqual(pipeline.duration("ffprobe", output / "master_podcast.wav"), 5, delta=0.05)


if __name__ == "__main__":
    unittest.main()

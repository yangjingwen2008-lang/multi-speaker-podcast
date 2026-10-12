from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / ".codex/skills/multi-speaker-podcast/scripts"
sys.path.insert(0, str(SCRIPTS))
import music_fetcher
import podcast_pipeline as pipeline
import run_state
import setup_ai_env
import validate_output


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="podcast stability ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "采访 ' $ sample.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(self.source)],
            check=True,
        )

    def test_failed_overwrite_does_not_retain_success_status(self):
        output = self.root / "output"
        args = pipeline.parse_args(["--input", str(self.source), "--output-dir", str(output), "--skip-ai"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pipeline.prepare_audio(args), 0)
        old_master = (output / "master_podcast.wav").read_bytes()
        args.overwrite = True
        args.music_mode = "provided"
        args.music_file = str(self.source)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            pipeline.prepare_audio(args)
        state = json.loads((output / "production_status.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        manifest = json.loads((output / "release_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["files"], [])
        self.assertEqual((output / "master_podcast.wav").read_bytes(), old_master)

    def test_lock_contention_does_not_modify_owner_status(self):
        output = self.root / "locked"
        output.mkdir()
        status = output / "production_status.json"
        status.write_text('{"status":"running","run_id":"owner"}', encoding="utf-8")
        with run_state.OutputLock(output):
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "podcast_pipeline.py"),
                    "--input",
                    str(self.source),
                    "--output-dir",
                    str(output),
                    "--skip-ai",
                    "--overwrite",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("locked by another", result.stderr)
            self.assertEqual(json.loads(status.read_text())["run_id"], "owner")

    def test_kernel_releases_lock_after_worker_crash(self):
        output = self.root / "crash"
        output.mkdir()
        code = "import sys,os; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import run_state; lock=run_state.OutputLock(Path(sys.argv[2])); lock.__enter__(); os._exit(7)"
        child = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(output)], timeout=30)
        self.assertEqual(child.returncode, 7)
        with run_state.OutputLock(output):
            self.assertTrue((output / ".pipeline.lock").exists())

    def test_atomic_journal_retries_transient_permission_error(self):
        target = self.root / "retry.json"
        original = Path.replace
        calls = []

        def flaky(source, destination):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError("Transient sharing violation")
            return original(source, destination)

        with patch.object(Path, "replace", flaky), patch.object(run_state.time, "sleep") as sleep:
            run_state.write_json(target, {"status": "completed"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads(target.read_text())["status"], "completed")

    def test_atomic_journal_retry_is_bounded(self):
        target = self.root / "bounded.json"
        with (
            patch.object(Path, "replace", side_effect=PermissionError("Denied")) as replace,
            patch.object(run_state.time, "sleep"),
            self.assertRaises(PermissionError),
        ):
            run_state.write_json(target, {})
        self.assertEqual(replace.call_count, 7)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_ai_setup_rejects_concurrent_modification(self):
        env_dir = self.root / "managed-ai"
        lock = self.root / ".managed-ai.setup-lock"
        lock.mkdir()
        args = argparse.Namespace(env_dir=str(env_dir), compute="cpu", dry_run=False)
        with run_state.OutputLock(lock), patch.object(setup_ai_env, "setup_env_unlocked") as install:
            report = setup_ai_env.setup_env(args)
        self.assertFalse(report.success)
        install.assert_not_called()
        self.assertIn("locked by another", report.validation["validation_error"])

    def test_unicode_cli_is_independent_of_console_encoding(self):
        output = self.root / "unicode_output"
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "ascii"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "podcast_pipeline.py"),
                "--input",
                str(self.source),
                "--output-dir",
                str(output),
                "--skip-ai",
            ],
            env=env,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("采访", result.stdout.decode("utf-8"))

    def test_corrupted_audio_reports_failure_without_traceback(self):
        source = self.root / "corrupted.wav"
        source.write_bytes(b"not audio")
        output = self.root / "corrupt_output"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "podcast_pipeline.py"),
                "--input",
                str(source),
                "--output-dir",
                str(output),
                "--skip-ai",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(json.loads((output / "run_state.json").read_text())["status"], "failed")

    def test_occupied_directory_is_preserved(self):
        output = self.root / "occupied"
        output.mkdir()
        manual = output / "manual.txt"
        manual.write_text("user work", encoding="utf-8")
        args = pipeline.parse_args(["--input", str(self.source), "--output-dir", str(output), "--skip-ai"])
        with self.assertRaises(ValueError):
            pipeline.prepare_audio(args)
        self.assertEqual(manual.read_text(), "user work")
        self.assertFalse((output / "failure_report.md").exists())

    def test_fault_diagnostics_redact_credentials(self):
        output = self.root / "redacted"
        secret = "hf_private_test_token_123"
        args = pipeline.parse_args(
            ["--input", str(self.source), "--output-dir", str(output), "--skip-ai", "--hf-token", secret]
        )
        with (
            patch.object(pipeline, "produce_audio", side_effect=RuntimeError(f"Failure {secret}")),
            self.assertRaises(RuntimeError),
        ):
            pipeline.prepare_audio(args)
        for path in output.rglob("*.json"):
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))
        self.assertNotIn(secret, (output / "failure_report.md").read_text(encoding="utf-8"))

    def test_music_network_failure_redacts_credentials(self):
        secret = "jamendo_test_private_client_id"
        with patch.object(music_fetcher, "jamendo_candidates", side_effect=RuntimeError(f"HTTP failure {secret}")):
            manifest = music_fetcher.fetch_music(self.root, "", "", "calm", "jamendo", secret)
        self.assertFalse(manifest["auto_mix_ready"])
        self.assertNotIn(secret, json.dumps(manifest))
        self.assertNotIn(secret, (self.root / "music_license_report.md").read_text(encoding="utf-8"))

    def test_completeness_audit_detects_missing_and_tampered_outputs(self):
        output = self.root / "complete"
        args = pipeline.parse_args(["--input", str(self.source), "--output-dir", str(output), "--skip-ai"])
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.prepare_audio(args)
        report = validate_output.audit(output)
        self.assertTrue(report["passed"], report)
        (output / "final_podcast.mp3").write_bytes(b"tampered")
        report = validate_output.audit(output, decode=False)
        self.assertFalse(report["passed"])
        self.assertFalse(next(row for row in report["checks"] if row["name"] == "release_hashes")["passed"])
        (output / "production_status.json").write_text("invalid json", encoding="utf-8")
        self.assertFalse(validate_output.audit(output, decode=False)["passed"])

    def test_optimized_python_cannot_bypass_acceptance_checks(self):
        result = subprocess.run(
            [sys.executable, "-O", str(SCRIPTS / "validate_output.py"), str(self.root)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["passed"])

    def test_three_repeated_runs_have_consistent_release_hashes(self):
        digests = []
        output = self.root / "repeat"
        args = pipeline.parse_args(["--input", str(self.source), "--output-dir", str(output), "--skip-ai"])
        for index in range(3):
            args.overwrite = index > 0
            with contextlib.redirect_stdout(io.StringIO()):
                pipeline.prepare_audio(args)
            report = validate_output.audit(output)
            self.assertTrue(report["passed"], report)
            manifest = json.loads((output / "release_manifest.json").read_text())
            digests.append([row["sha256"] for row in manifest["files"]])
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(digests[1], digests[2])

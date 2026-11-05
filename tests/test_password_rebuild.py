import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import termios
import time
import unittest


class PasswordRebuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ciri-password-test.")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "images").mkdir()
        (self.root / "hashes").mkdir()
        source = Path(__file__).resolve().parent.parent / "scripts/rebuild-ciri-with-password.sh"
        self.script = self.root / "scripts" / source.name
        shutil.copy2(source, self.script)
        (self.root / "scripts/bundle-ciri-arch.sh").write_text('''#!/usr/bin/env bash
python3 -I - "$@" <<'PY'
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
captured = {'args': args}
if '--password-hash-file' in args:
    path = Path(args[args.index('--password-hash-file') + 1])
    captured.update(hash=path.read_text().strip(), mode=path.stat().st_mode & 0o777, hash_path=str(path))
Path(os.environ['TEST_CAPTURE']).write_text(json.dumps(captured))
sys.exit(int(os.environ.get('TEST_EXIT', '0')))
PY
''')
        self.capture = self.root / "capture.json"
        self.env = dict(os.environ, TMPDIR=str(self.root / "hashes"), TEST_CAPTURE=str(self.capture))

    def command(self, *args):
        return ["bash", str(self.script), "--sku", "0", *args]

    def interactive(self, password, confirmation=None, exit_code=0):
        master, slave = pty.openpty()

        def terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(
            self.command(), stdin=slave, stdout=slave, stderr=slave,
            env=dict(self.env, TEST_EXIT=str(exit_code)), preexec_fn=terminal,
        )
        os.close(slave)
        transcript = bytearray()

        def wait_for(prompt):
            deadline = time.monotonic() + 10
            while prompt not in transcript:
                left = deadline - time.monotonic()
                self.assertGreater(left, 0, bytes(transcript))
                ready, _, _ = select.select([master], [], [], left)
                self.assertTrue(ready, bytes(transcript))
                transcript.extend(os.read(master, 4096))

        try:
            wait_for(b"Root/alarm console password: ")
            os.write(master, password + b"\n")
            if confirmation is not None:
                wait_for(b"Confirm password: ")
                os.write(master, confirmation + b"\n")
            result = process.wait(timeout=10)
            while select.select([master], [], [], 0)[0]:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                transcript.extend(data)
            return result, bytes(transcript)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_password_hash_and_cleanup(self):
        password = b"Test $ password \\ with spaces"
        code, transcript = self.interactive(password, password)
        self.assertEqual(code, 0, transcript)
        self.assertNotIn(password, transcript)
        captured = json.loads(self.capture.read_text())
        self.assertNotIn("--ssh-key", captured["args"])
        self.assertEqual(captured["mode"], 0o600)
        self.assertTrue(captured["hash"].startswith("$6$"))
        salt = captured["hash"].split("$")[2]
        expected = subprocess.check_output(
            ["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input=password + b"\n",
        ).decode().strip()
        self.assertEqual(captured["hash"], expected)
        self.assertFalse(Path(captured["hash_path"]).exists())
        self.assertFalse(list((self.root / "hashes").iterdir()))

    def test_blank_password_skips_prompt_and_hash(self):
        result = subprocess.run(self.command("--blank-root-password", "--console-rotation", "3", "--rootfs-cache", str(self.root / "rootfs-cache"), "--refresh-rootfs"), env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(self.capture.read_text())
        self.assertIn("--blank-root-password", captured["args"])
        self.assertEqual(captured["args"][captured["args"].index("--console-rotation") + 1], "3")
        self.assertIn("--refresh-rootfs", captured["args"])
        self.assertEqual(captured["args"][captured["args"].index("--rootfs-cache") + 1], str(self.root / "rootfs-cache"))
        self.assertNotIn("--password-hash-file", captured["args"])
        self.assertNotIn("--ssh-key", captured["args"])
        self.assertNotIn("Confirm password:", result.stderr)
        self.assertFalse(list((self.root / "hashes").iterdir()))

    def test_blank_password_conflicting_credentials_rejected(self):
        builder = Path(__file__).resolve().parent.parent / "scripts/bundle-ciri-arch.sh"
        for option in ("--ssh-key", "--password-hash-file"):
            with self.subTest(option=option):
                result = subprocess.run([
                    "bash", str(builder), "--sku", "0", "--kernel-dir", "unused",
                    "--firmware-dir", "unused", "--cgpt-dir", "unused", "--output", "unused.img",
                    "--blank-root-password", option, "unused",
                ], text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot be combined", result.stderr)

    def test_builder_failure_cleans_hash(self):
        code, transcript = self.interactive(b"test-password", b"test-password", exit_code=23)
        self.assertEqual(code, 23, transcript)
        self.assertFalse(list((self.root / "hashes").iterdir()))

    def test_mismatch_and_empty_password(self):
        for password, confirmation in ((b"first-password", b"different-password"), (b"", None)):
            with self.subTest(password=password):
                code, transcript = self.interactive(password, confirmation)
                self.assertNotEqual(code, 0, transcript)
                self.assertFalse(self.capture.exists())
                self.assertFalse(list((self.root / "hashes").iterdir()))

    def test_existing_image_and_ssh_key_refused_before_prompt(self):
        (self.root / "images/arch-ciri-password.img").touch()
        for args, message in (((), "Refusing to replace"), (("--ssh-key", "unused.pub"), "do not supply an SSH key")):
            with self.subTest(args=args):
                result = subprocess.run(self.command(*args), env=self.env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("console password:", result.stderr)
                self.assertFalse(self.capture.exists())


if __name__ == "__main__":
    unittest.main()

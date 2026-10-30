import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/remote-build.py"
spec = importlib.util.spec_from_file_location("remote_build", SCRIPT)
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


class ChecksumTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "tool").write_bytes(b"test artifact")
        digest = hashlib.sha256(b"test artifact").hexdigest()
        (self.artifacts / "SHA256SUMS").write_text(f"{digest}  tool\n")

    def test_valid_artifact(self):
        remote.verify_checksums(self.artifacts)

    def test_tampered_artifact(self):
        (self.artifacts / "tool").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            remote.verify_checksums(self.artifacts)

    def test_empty_manifest(self):
        (self.artifacts / "SHA256SUMS").write_text("")
        with self.assertRaisesRegex(ValueError, "Empty"):
            remote.verify_checksums(self.artifacts)

    def test_path_escape(self):
        (self.root / "outside").write_bytes(b"test artifact")
        digest = hashlib.sha256(b"test artifact").hexdigest()
        (self.artifacts / "SHA256SUMS").write_text(f"{digest}  ../outside\n")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            remote.verify_checksums(self.artifacts)

    def test_symlink_escape(self):
        (self.root / "outside").write_bytes(b"test artifact")
        (self.artifacts / "tool").unlink()
        (self.artifacts / "tool").symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            remote.verify_checksums(self.artifacts)


class DockerEndpointTests(unittest.TestCase):
    def test_loopback_only(self):
        for endpoint in ("http://127.0.0.1:23755", "http://localhost:1234", "http://[::1]:1234"):
            with self.subTest(endpoint=endpoint):
                remote.Docker(endpoint)
        for endpoint in ("http://example.com:2375", "http://127.0.0.1/x", "http://user:pass@localhost:2375", "http://localhost?x=1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                remote.Docker(endpoint)

    def test_invalid_arguments_fail_before_connecting(self):
        for arguments in (("--name", "../outside"), ("--jobs", "0"), ("--memory-gib", "0"), ("--revision", "main")):
            result = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--script", str(SCRIPT),
                 "--source-url", "https://example.invalid/repository", *arguments],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Connection refused", result.stderr)


if __name__ == "__main__":
    unittest.main()

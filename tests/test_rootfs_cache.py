from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


script = Path(__file__).resolve().parent.parent / "scripts/arch-rootfs-cache.py"
spec = importlib.util.spec_from_file_location("arch_rootfs_cache", script)
cache_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_module)


class RootfsCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ciri-rootfs-cache-test.")
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / "cache"
        self.payload = b"rootfs test fixture, never extracted or executed"

        def download(snapshot):
            (snapshot / cache_module.CACHED_FILES[0]).write_bytes(self.payload)
            (snapshot / "rootfs.sig").write_bytes(b"signature fixture")
            (snapshot / "signing-keys.gpg").write_bytes(b"public-key fixture")

        self.download = self.enterContext(patch.object(cache_module, "download_signed_rootfs", side_effect=download))
        self.verify = self.enterContext(patch.object(cache_module, "verify_signature", return_value=cache_module.SIGNING_KEYS[0]))

    def test_reuse_without_redownloading(self):
        first = cache_module.ensure_rootfs(self.cache)
        second = cache_module.ensure_rootfs(self.cache)
        self.assertEqual(first, second)
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(self.verify.call_count, 2)
        self.assertEqual(Path(first["rootfs"]).read_bytes(), self.payload)

    def test_refresh_preserves_previous_snapshot(self):
        first = cache_module.ensure_rootfs(self.cache)
        self.payload = b"new signed rootfs fixture"
        second = cache_module.ensure_rootfs(self.cache, refresh=True)
        self.assertNotEqual(first["rootfs"], second["rootfs"])
        self.assertTrue(Path(first["rootfs"]).is_file())
        self.assertEqual(Path(second["rootfs"]).read_bytes(), self.payload)
        self.assertEqual(cache_module.ensure_rootfs(self.cache), second)
        self.assertEqual(self.download.call_count, 2)

    def test_failed_refresh_keeps_previous_cache(self):
        first = cache_module.ensure_rootfs(self.cache)
        pointer = (self.cache / "current.json").read_bytes()
        self.download.side_effect = RuntimeError("interrupted download")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            cache_module.ensure_rootfs(self.cache, refresh=True)
        self.assertEqual((self.cache / "current.json").read_bytes(), pointer)
        self.assertEqual(cache_module.ensure_rootfs(self.cache), first)
        self.assertFalse(list(self.cache.glob(".download.*")))

    def test_initial_signature_failure_does_not_publish(self):
        self.verify.side_effect = subprocess.CalledProcessError(2, ["gpg"])
        with self.assertRaises(subprocess.CalledProcessError):
            cache_module.ensure_rootfs(self.cache)
        self.assertFalse((self.cache / "current.json").exists())
        self.assertFalse(list(self.cache.glob("rootfs-*")))
        self.assertFalse(list(self.cache.glob(".download.*")))

    def test_tampering_is_rejected_without_network(self):
        first = cache_module.ensure_rootfs(self.cache)
        snapshot = Path(first["rootfs"]).parent
        for filename in cache_module.CACHED_FILES:
            with self.subTest(filename=filename):
                path = snapshot / filename
                original = path.read_bytes()
                path.write_bytes(original + b"tampered")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    cache_module.ensure_rootfs(self.cache)
                path.write_bytes(original)
        self.assertEqual(self.download.call_count, 1)

    def test_symlink_and_invalid_selector_are_rejected(self):
        first = cache_module.ensure_rootfs(self.cache)
        archive = Path(first["rootfs"])
        target = self.cache / "unrelated"
        target.write_bytes(self.payload)
        archive.unlink()
        archive.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Unsafe or missing cache file"):
            cache_module.ensure_rootfs(self.cache)
        for pointer in ({"snapshot": "../outside"}, [], {"snapshot": 1}):
            with self.subTest(pointer=pointer):
                (self.cache / "current.json").write_text(json.dumps(pointer))
                with self.assertRaisesRegex(ValueError, "Invalid cache snapshot selector"):
                    cache_module.ensure_rootfs(self.cache)
        self.assertEqual(self.download.call_count, 1)

    def test_concurrent_builds_download_only_once(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: cache_module.ensure_rootfs(self.cache), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.download.call_count, 1)


class SignatureTests(unittest.TestCase):
    def test_pinned_primary_key_is_accepted(self):
        status = f"[GNUPG:] VALIDSIG {'A' * 40} 2026-11-03 0 0 4 0 1 10 00 {cache_module.SIGNING_KEYS[0]}\n"
        self.assertEqual(cache_module.valid_signer(status), cache_module.SIGNING_KEYS[0])
        with self.assertRaises(ValueError):
            cache_module.valid_signer(f"[GNUPG:] VALIDSIG {'A' * 40}\n")

    def test_cached_verification_disables_network_and_user_keyring(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot"
            snapshot.mkdir()
            status = f"[GNUPG:] VALIDSIG {cache_module.SIGNING_KEYS[0]}\n"
            with patch.object(cache_module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=status)) as run:
                self.assertEqual(cache_module.verify_signature(snapshot), cache_module.SIGNING_KEYS[0])
                command = run.call_args.args[0]
                for flag in ("--no-options", "--no-auto-key-retrieve", "--no-auto-key-import", "--no-default-keyring"):
                    self.assertIn(flag, command)
                self.assertNotIn("--recv-keys", command)
                self.assertEqual(command[command.index("--keyring") + 1], str(snapshot / "signing-keys.gpg"))


if __name__ == "__main__":
    unittest.main()

"""Exercise the recipe's config guard, not a kernel build or camera hardware."""

import ast
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
RECIPE = ROOT / "scripts/build-ciri-kernel.sh"
CAMERA_OPTIONS = (
    "MTK_SCP", "RPMSG_MTK_SCP", "VIDEO_MTK_ISP_71_CAMSYS",
    "VIDEO_GC05A2", "VIDEO_GC08A3", "VIDEO_DW9768",
)


class KernelCameraConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = RECIPE.read_text()
        # Test the actual inline guard with a synthetic config at a private path.
        guards = re.findall(r"python3 -I -c '\n(.*?)\n'", cls.recipe, re.DOTALL)
        if len(guards) != 1:
            raise AssertionError("Expected exactly one inline kernel config guard")
        cls.guard = guards[0]
        cls.required = []
        for node in ast.parse(cls.guard).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "required"):
                cls.required = ast.literal_eval(node.value)
            elif (isinstance(node, ast.AugAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "required"
                    and isinstance(node.op, ast.Add)):
                cls.required += ast.literal_eval(node.value)
        if not cls.required:
            raise AssertionError("Kernel config guard has no required options")

    def run_guard(self, settings):
        with tempfile.TemporaryDirectory(prefix="ciri-camera-config-test.") as directory:
            config = Path(directory) / "kernel.config"
            config.write_text("".join(
                f"# CONFIG_{name} is not set\n" if value is None
                else f"CONFIG_{name}={value}\n"
                for name, value in settings.items()
            ))
            self.assertEqual(self.guard.count('"/work/out/.config"'), 1)
            guard = self.guard.replace('"/work/out/.config"', repr(str(config)))
            return subprocess.run(
                [sys.executable, "-I", "-c", guard],
                text=True, capture_output=True, check=False,
            )

    def test_camera_modules_requested_and_checked_after_olddefconfig(self):
        for name in CAMERA_OPTIONS:
            with self.subTest(name=name):
                self.assertRegex(self.recipe, rf"--module {name}(?:\s|$)")
                self.assertIn(name, self.required)
        self.assertLess(
            self.recipe.index("make O=/work/out olddefconfig"),
            self.recipe.index("required +="),
        )
        self.assertLess(
            self.recipe.index("Required Ciri options disabled after olddefconfig"),
            self.recipe.index('make O=/work/out -j"$jobs" Image'),
        )

    def test_enabled_camera_modules_accepted(self):
        settings = dict.fromkeys(self.required, "y")
        settings.update(dict.fromkeys(CAMERA_OPTIONS, "m"))
        result = self.run_guard(settings)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_builtin_camera_drivers_accepted(self):
        result = self.run_guard(dict.fromkeys(self.required, "y"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_disabled_camera_option_rejected(self):
        for name in CAMERA_OPTIONS:
            with self.subTest(name=name):
                settings = dict.fromkeys(self.required, "y")
                settings[name] = None
                result = self.run_guard(settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"MISSING: CONFIG_{name}", result.stdout)
                self.assertIn("Required Ciri options disabled after olddefconfig", result.stderr)
                self.assertIn(name, result.stderr)


if __name__ == "__main__":
    unittest.main()

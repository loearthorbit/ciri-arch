"""Preview-only tests: never access audio hardware or install system files."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/install-ciri-audio.sh"
UCM = Path("usr/share/alsa/ucm2/conf.d/sof-mt8188_m983")


class AudioInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ciri-audio-test.")
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / "preview"

    def preview(self):
        return subprocess.run(
            ["bash", str(SCRIPT), "--stage", str(self.stage)],
            text=True, capture_output=True, check=True,
        )

    def test_self_contained_analog_profile(self):
        result = self.preview()
        self.assertIn("Preview only", result.stdout)
        hifi = (self.stage / UCM / "HiFi.conf").read_text()
        self.assertIn('SectionDevice."Speaker"', hifi)
        self.assertIn('SectionDevice."Headphones"', hifi)
        self.assertIn('PlaybackPCM "hw:${CardId},16"', hifi)
        self.assertIn('PlaybackPCM "hw:${CardId},17"', hifi)
        self.assertNotIn('SectionDevice."HDMI"', hifi)
        self.assertNotIn("DPTX_OUT_MUX", hifi)
        self.assertNotIn('PlaybackPCM "hw:${CardId},5"', hifi)
        verb = hifi.split('SectionDevice."Speaker"')[0]
        for control in ("O072 I070 Switch", "O073 I071 Switch", "O048 I020 Switch", "O049 I021 Switch"):
            self.assertIn(control, verb)
        short = self.stage / UCM / "sof-mt8188_m983.conf"
        long = self.stage / UCM / "sof-mt8188_m98390_5682.conf"
        self.assertEqual(short.read_bytes(), long.read_bytes())
        self.assertIn('SectionUseCase."HiFi"', long.read_text())

    def test_current_kernel_driver_lookup(self):
        self.preview()
        # Modern ucm.conf looks up CardLongName under CardDriver, not CardId.
        driver = "mt8188_mt6359"
        long_name = "sof-mt8188_m98390_5682"
        directory = self.stage / "usr/share/alsa/ucm2/conf.d" / driver
        profile = directory / f"{long_name}.conf"
        self.assertEqual(
            profile.read_bytes(),
            (self.stage / UCM / f"{long_name}.conf").read_bytes(),
        )
        self.assertEqual(profile.read_bytes(), (directory / f"{driver}.conf").read_bytes())
        self.assertIn('File "HiFi.conf"', profile.read_text())
        self.assertEqual(
            (profile.parent / "HiFi.conf").read_bytes(),
            (self.stage / UCM / "HiFi.conf").read_bytes(),
        )

    def test_ucm_validation_resolves_hardware_card_id(self):
        # snd_use_case_mgr_open matches bare names to driver/name/longname,
        # NOT card IDs. Both validation calls must explicitly request hardware.
        commands = re.findall(r"^alsaucm -c (\S+)", SCRIPT.read_text(), re.M)
        self.assertEqual(commands, ["hw:sofmt8188m98390", "hw:sofmt8188m98390"])

    def test_calibration_safe_fallback_and_syntax(self):
        self.preview()
        helper = self.stage / "usr/libexec/ciri-speaker-calibration"
        content = helper.read_text()
        self.assertIn("SAFE_VOLUME=138", content)
        self.assertIn("NORMAL_VOLUME=160", content)
        self.assertIn("VPD=/sys/firmware/vpd/ro", content)
        self.assertIn("RDC_MIN=5950", content)
        self.assertIn("RDC_MAX=8050", content)
        safe_write = content.index('cset name="$amp Digital Volume" "$SAFE_VOLUME"')
        calibration_write = content.index('cset name="$amp DSM Rdc"')
        self.assertLess(safe_write, calibration_write)
        self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
        subprocess.run(["sh", "-n", str(helper)], check=True)

    def test_rule_service_and_license(self):
        self.preview()
        rule = self.stage / "etc/wireplumber/wireplumber.conf.d/51-ciri-disable-pro-audio.conf"
        self.assertIn("api.acp.disable-pro-audio = true", rule.read_text())
        service = self.stage / "usr/lib/systemd/system/ciri-speaker-calibration.service"
        self.assertIn("After=alsa-restore.service alsa-state.service sound.target", service.read_text())
        self.assertIn("ExecStart=/usr/libexec/ciri-speaker-calibration", service.read_text())
        license_file = self.stage / "usr/share/doc/ciri-audio/LICENSE.upstream"
        self.assertIn("Copyright (c) 2026 Michał Kopeć", license_file.read_text())
        provenance = self.stage / "usr/share/doc/ciri-audio/provenance.txt"
        self.assertIn("542342f9d1bd30201b42913796840e8fa471049a", provenance.read_text())

    def test_existing_preview_refused_without_overwrite(self):
        self.preview()
        config = self.stage / UCM / "HiFi.conf"
        config.write_text("keep this existing content\n")
        result = subprocess.run(
            ["bash", str(SCRIPT), "--stage", str(self.stage)],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(config.read_text(), "keep this existing content\n")

    def test_failed_install_reports_stopped_audio_without_masking_error(self):
        # Exercise only the EXIT handler: no system files, hardware or services.
        match = re.search(r"^report_exit\(\) \{\n.*?^\}", SCRIPT.read_text(), re.M | re.S)
        self.assertIsNotNone(match)
        for stopped, status in ((1, 7), (0, 7), (1, 0)):
            with self.subTest(stopped=stopped, status=status):
                result = subprocess.run(
                    ["bash", "-c", f"{match.group()}\naudio_stopped={stopped}\ntrap report_exit EXIT\nexit {status}\n"],
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, status)
                if stopped and status:
                    self.assertIn("not successfully restarted", result.stderr)
                    self.assertIn("systemctl --user start", result.stderr)
                    self.assertIn("pactl set-card-profile", result.stderr)
                    self.assertIn("Do not test speakers", result.stderr)
                else:
                    self.assertEqual(result.stderr, "")

    def test_invalid_arguments_refused(self):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--unexpected"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()

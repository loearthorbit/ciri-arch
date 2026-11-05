#!/bin/bash
# Self-contained tablet-side Ciri audio installer. No network or kernel rebuild.
# sudo bash install-ciri-audio.sh
# Nonprivileged preview/testing: bash install-ciri-audio.sh --stage NEW_DIRECTORY
# UCM/calibration/rule adapted from mkopec/ciri-mainline at:
# 542342f9d1bd30201b42913796840e8fa471049a (MIT; license included in payload).
set -euo pipefail
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
mode=install
if [[ $# -eq 2 && $1 == --stage ]]; then
    mode=stage
    stage=$2
    [[ ! -e "$stage" && ! -L "$stage" ]] || fail 'Preview directory must not already exist.'
elif [[ $# -ne 0 ]]; then
    fail 'Usage: sudo bash install-ciri-audio.sh [or: --stage NEW_DIRECTORY]'
fi
command -v python3 >/dev/null || fail 'Install python first: pacman -Syu --needed python'
if [[ $mode == install ]]; then
    [[ $EUID -eq 0 ]] || fail 'Run this installer with sudo on the Ciri tablet.'
    for cmd in amixer alsaucm systemctl mktemp runuser timeout; do
        command -v "$cmd" >/dev/null || fail "Missing $cmd; install alsa-utils, alsa-ucm-conf and systemd first."
    done
    [[ -e /usr/share/alsa/ucm2/lib/card-init.conf ]] || fail 'Install alsa-ucm-conf first.'
    stage=$(mktemp -d /var/tmp/ciri-audio-install.XXXXXXXX)
fi

python3 -I - "$stage" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
main = '''# Google Ciri: MT8188 + MAX98390 + RT5682S, SOF topology.
Syntax 4
SectionUseCase."HiFi" {
    File "HiFi.conf"
    Comment "Ciri speakers, headset and microphones (analog only)"
}
Include.card-init.File "/lib/card-init.conf"
'''
hifi = '''# Derived from mkopec/ciri-mainline, revision 542342f9d1bd30201b42913796840e8fa471049a.
# Analog-only: experimental DisplayPort device and DPTX mixer controls omitted.
# Routes must be connected in the verb BEFORE PipeWire ACP probes the SOF PCMs;
# otherwise the AFE may remain runtime-suspended and hw_params fails with EINVAL.
SectionVerb {
    Value.TQ "HiFi"
    EnableSequence [
        cdev "hw:${CardId}"
        cset "name='ETDM_IN1_Clock_Source' a1sys_a2sys"
        cset "name='ETDM_OUT1_Clock_Source' a1sys_a2sys"
        cset "name='ETDM_IN2_Clock_Source' a1sys_a2sys"
        cset "name='ETDM_OUT2_Clock_Source' a1sys_a2sys"
        cset "name='ETDM_OUT3_Clock_Source' a1sys_a2sys"
        cset "name='UL_SRC_MUX' DMIC"
        cset "name='UL2_SRC_MUX' DMIC"
        cset "name='DMIC0_MUX' DMIC_DATA0"
        cset "name='DMIC2_MUX' DMIC_DATA1_L"
        cset "name='MTKAIF_DMIC Switch' On"
        cset "name='MISO0_MUX' UL1_CH1"
        cset "name='MISO1_MUX' UL1_CH2"
        cset "name='Front Left DAI Sel Mux' Left"
        cset "name='Front Right DAI Sel Mux' Right"
        cset "name='DAC1 Playback Volume' 120"
        cset "name='STO1 ADC Capture Volume' 50"
        cset "name='O072 I070 Switch' 1"
        cset "name='O073 I071 Switch' 1"
        cset "name='O048 I020 Switch' 1"
        cset "name='O049 I021 Switch' 1"
        cset "name='O034 I168 Switch' 1"
        cset "name='O035 I169 Switch' 1"
        cset "name='O036 I012 Switch' 1"
        cset "name='O037 I013 Switch' 1"
    ]
    DisableSequence [
        cdev "hw:${CardId}"
        cset "name='O072 I070 Switch' 0"
        cset "name='O073 I071 Switch' 0"
        cset "name='O048 I020 Switch' 0"
        cset "name='O049 I021 Switch' 0"
        cset "name='O034 I168 Switch' 0"
        cset "name='O035 I169 Switch' 0"
        cset "name='O036 I012 Switch' 0"
        cset "name='O037 I013 Switch' 0"
        cset "name='MTKAIF_DMIC Switch' Off"
    ]
}
SectionDevice."Speaker" {
    Comment "Internal speakers"
    Value {
        PlaybackPCM "hw:${CardId},16"
        PlaybackChannels 2
        PlaybackPriority 100
    }
    EnableSequence [
        cdev "hw:${CardId}"
        cset "name='O072 I070 Switch' 1"
        cset "name='O073 I071 Switch' 1"
    ]
    DisableSequence [
        cdev "hw:${CardId}"
        cset "name='O072 I070 Switch' 0"
        cset "name='O073 I071 Switch' 0"
    ]
}
SectionDevice."Headphones" {
    Comment "Headphone jack"
    Value {
        PlaybackPCM "hw:${CardId},17"
        PlaybackChannels 2
        PlaybackPriority 200
        JackControl "Headphone Jack"
    }
    EnableSequence [
        cdev "hw:${CardId}"
        cset "name='O048 I020 Switch' 1"
        cset "name='O049 I021 Switch' 1"
        cset "name='Stereo1 DAC MIXL DAC L1 Switch' 1"
        cset "name='Stereo1 DAC MIXR DAC R1 Switch' 1"
    ]
    DisableSequence [
        cdev "hw:${CardId}"
        cset "name='O048 I020 Switch' 0"
        cset "name='O049 I021 Switch' 0"
        cset "name='Stereo1 DAC MIXL DAC L1 Switch' 0"
        cset "name='Stereo1 DAC MIXR DAC R1 Switch' 0"
    ]
}
SectionDevice."InternalMic" {
    Comment "Internal digital microphones"
    Value {
        CapturePCM "hw:${CardId},18"
        CaptureChannels 2
        CapturePriority 200
    }
    EnableSequence [
        cdev "hw:${CardId}"
        cset "name='MTKAIF_DMIC Switch' On"
        cset "name='O034 I168 Switch' 1"
        cset "name='O035 I169 Switch' 1"
        cset "name='MISO0_MUX' UL1_CH1"
        cset "name='MISO1_MUX' UL1_CH2"
    ]
    DisableSequence [
        cdev "hw:${CardId}"
        cset "name='O034 I168 Switch' 0"
        cset "name='O035 I169 Switch' 0"
        cset "name='MTKAIF_DMIC Switch' Off"
    ]
}
SectionDevice."Mic" {
    Comment "Headset microphone"
    Value {
        CapturePCM "hw:${CardId},19"
        CaptureChannels 2
        CapturePriority 100
        JackControl "Headset Mic Jack"
    }
    EnableSequence [
        cdev "hw:${CardId}"
        cset "name='O036 I012 Switch' 1"
        cset "name='O037 I013 Switch' 1"
        cset "name='CBJ Boost Volume' 15"
        cset "name='RECMIX1L CBJ Switch' 1"
        cset "name='RECMIX1R CBJ Switch' 1"
        cset "name='Stereo1 ADC L1 Mux' 1"
        cset "name='Stereo1 ADC R1 Mux' 1"
        cset "name='Stereo1 ADC MIXL ADC1 Switch' 1"
        cset "name='Stereo1 ADC MIXR ADC1 Switch' 1"
    ]
    DisableSequence [
        cdev "hw:${CardId}"
        cset "name='O036 I012 Switch' 0"
        cset "name='O037 I013 Switch' 0"
        cset "name='RECMIX1L CBJ Switch' 0"
        cset "name='RECMIX1R CBJ Switch' 0"
        cset "name='Stereo1 ADC L1 Mux' 0"
        cset "name='Stereo1 ADC R1 Mux' 0"
        cset "name='Stereo1 ADC MIXL ADC1 Switch' 0"
        cset "name='Stereo1 ADC MIXR ADC1 Switch' 0"
    ]
}
'''
calibration = '''#!/bin/sh
# Adapted from mkopec/ciri-mainline (MIT). Read ONLY this tablet's factory VPD.
# Missing/invalid calibration uses -11 dB digital gain, never made-up values.
set -u
CARD_ID=sofmt8188m98390
VPD=/sys/firmware/vpd/ro
NORMAL_VOLUME=160
SAFE_VOLUME=138
RDC_MIN=5950
RDC_MAX=8050
log() { printf 'ciri-speaker-calibration: %s\\n' "$*" >&2; }
card=
for i in $(seq 50); do
    for f in /sys/class/sound/card*/id; do
        [ -r "$f" ] && [ "$(cat "$f")" = "$CARD_ID" ] && card=$(cat "$f")
    done
    [ -n "$card" ] && break
    sleep 0.2
done
if [ -z "$card" ]; then
    log "sound card $CARD_ID not found"
    exit 1
fi
modprobe -q vpd-sysfs 2>/dev/null || true
status=0
for n in 0 1; do
    case $n in 0) amp="Front Left" ;; 1) amp="Front Right" ;; esac
    volume=$SAFE_VOLUME
    # Reduce gain BEFORE changing calibration or parsing any VPD values.
    if ! amixer -q -c "$card" cset name="$amp Digital Volume" "$SAFE_VOLUME"; then
        log "$amp: could not set safe gain"
        status=1
        continue
    fi
    r0=$(cat "$VPD/dsm_calib_r0_$n" 2>/dev/null) || r0=
    temp=$(cat "$VPD/dsm_calib_temp_$n" 2>/dev/null) || temp=
    case "$r0" in ''|*[!0-9]*) r0=0 ;; esac
    while [ "${r0#0}" != "$r0" ]; do r0=${r0#0}; done
    r0=${r0:-0}
    # Reject malformed/oversized values before shell arithmetic.
    [ "${#r0}" -le 9 ] || r0=0
    case "$temp" in ''|*[!0-9]*) temp= ;; esac
    if [ "$r0" -le 0 ] 2>/dev/null || [ -z "$temp" ]; then
        log "$amp: missing/invalid VPD calibration; using safe mode volume (-11 dB)"
    else
        rdc=$((3837952000 / r0))
        if [ "$rdc" -lt "$RDC_MIN" ] || [ "$rdc" -gt "$RDC_MAX" ]; then
            log "$amp: resistance ${rdc} mohm out of range; using safe mode volume"
        elif amixer -q -c "$card" cset name="$amp DSM Rdc" "$r0" &&
             amixer -q -c "$card" cset name="$amp DSM Ambient Temp" "$temp"; then
            log "$amp: calibrated, ${rdc} mohm"
            volume=$NORMAL_VOLUME
        else
            log "$amp: calibration failed; using safe mode volume"
            status=1
        fi
    fi
    amixer -q -c "$card" cset name="$amp Digital Volume" "$volume" || status=1
done
exit "$status"
'''
service = '''[Unit]
Description=Apply Ciri speaker amplifier calibration (safe fallback without VPD)
After=alsa-restore.service alsa-state.service sound.target
Wants=sound.target
[Service]
Type=oneshot
ExecStart=/usr/libexec/ciri-speaker-calibration
[Install]
WantedBy=sound.target
'''
rule = '''# MT8188 SOF: raw Pro Audio probing can trigger AFE/ASoC EINVAL errors.
monitor.alsa.rules = [
  {
    matches = [ { device.name = "alsa_card.platform-sound" } ]
    actions = { update-props = { api.acp.disable-pro-audio = true } }
  }
]
'''
license_text = '''MIT License

Copyright (c) 2026 Michał Kopeć

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''
base = 'usr/share/alsa/ucm2/conf.d/sof-mt8188_m983/'
# This pinned kernel reports ALSA CardDriver=mt8188_mt6359. ALSA's ucm.conf
# searches conf.d/${CardDriver}/${CardLongName}.conf, not just the upstream
# short-name directory. Keep both mappings for the supported driver variants.
driver_base = 'usr/share/alsa/ucm2/conf.d/mt8188_mt6359/'
files = {
    base+'sof-mt8188_m983.conf': main,
    base+'sof-mt8188_m98390_5682.conf': main,
    base+'HiFi.conf': hifi,
    driver_base+'mt8188_mt6359.conf': main,
    driver_base+'sof-mt8188_m98390_5682.conf': main,
    driver_base+'HiFi.conf': hifi,
    'usr/libexec/ciri-speaker-calibration': calibration,
    'usr/lib/systemd/system/ciri-speaker-calibration.service': service,
    'etc/wireplumber/wireplumber.conf.d/51-ciri-disable-pro-audio.conf': rule,
    'usr/share/doc/ciri-audio/LICENSE.upstream': license_text,
    'usr/share/doc/ciri-audio/provenance.txt':
        'Source: https://github.com/mkopec/ciri-mainline\n'
        'Revision: 542342f9d1bd30201b42913796840e8fa471049a\n'
        'Changes: analog-only UCM; no experimental DP PCM/control probing; '
        'system-wide WirePlumber rule; calibration input validation and safe fallback.\n',
}
for name, content in files.items():
    dest = root/name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    dest.chmod(0o755 if name == 'usr/libexec/ciri-speaker-calibration' else 0o644)
print(f'Prepared {len(files)} audio configuration files in {root}', flush=True)
PY
if [[ $mode == stage ]]; then
    printf 'Preview only: no audio controls, services or system files changed.\n'
    exit 0
fi

# Check card, controls, firmware and conflicts BEFORE changing the live system.
python3 -I - "$stage" <<'PY'
from pathlib import Path
import re
import subprocess
import sys
stage = Path(sys.argv[1])
card = 'sofmt8188m98390'
assert any(p.read_text().strip() == card for p in Path('/sys/class/sound').glob('card*/id')), 'Expected Ciri sound card not found'
for name in ('mediatek/sof/sof-mt8188.ri', 'mediatek/sof-tplg/sof-mt8188.tplg'):
    assert any(Path('/usr/lib/firmware', name+suffix).is_file() for suffix in ('', '.zst', '.xz')), f'Missing audio firmware: {name}'
controls = subprocess.check_output(['amixer', '-c', card, 'controls'], text=True)
hifi = (stage/'usr/share/alsa/ucm2/conf.d/sof-mt8188_m983/HiFi.conf').read_text()
required = set(re.findall(r"cset \"name='([^']+)'", hifi))
required.update(('Front Left Digital Volume', 'Front Right Digital Volume'))
missing = sorted(name for name in required if f"name='{name}'" not in controls)
assert not missing, f'Kernel mixer controls missing; nothing installed: {missing}'
for source in stage.rglob('*'):
    if not source.is_file():
        continue
    dest = Path('/')/source.relative_to(stage)
    assert not dest.is_symlink(), f'Target is a symlink; inspect it first: {dest}'
    if dest.exists():
        assert dest.is_file() and dest.read_bytes() == source.read_bytes(), f'Conflicting existing file; inspect/remove explicitly first: {dest}'
print('Card, mixer controls, firmware and target paths checked', flush=True)
PY

# Use the invoking user's actual session, never a root PipeWire instance.
desktop_user=${SUDO_USER:-}
user_session=0
if [[ -n $desktop_user && $desktop_user != root ]]; then
    desktop_uid=$(id -u "$desktop_user")
    if [[ -S /run/user/$desktop_uid/bus ]]; then
        user_session=1
    fi
fi
user_command() {
    timeout 30s runuser -u "$desktop_user" -- env \
        XDG_RUNTIME_DIR="/run/user/$desktop_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$desktop_uid/bus" "$@"
}
audio_stopped=0
report_exit() {
    local status=$?
    if [[ $status != 0 && $audio_stopped == 1 ]]; then
        printf '\nUser audio was stopped for installation and was not successfully restarted.\n' >&2
        printf 'Fix the reported error and rerun this installer to finish setup.\n' >&2
        printf 'Only after calibration and HiFi validation succeed, manual recovery in your GNOME terminal WITHOUT sudo:\n' >&2
        printf '  systemctl --user start pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service\n' >&2
        printf '  pactl set-card-profile alsa_card.platform-sound HiFi\n' >&2
        printf 'Do not test speakers while calibration or UCM validation is failing.\n' >&2
    fi
    return "$status"
}
trap report_exit EXIT
if [[ $user_session == 1 ]]; then
    audio_stopped=1
    user_command systemctl --user stop wireplumber.service pipewire-pulse.service pipewire.service pipewire.socket pipewire-pulse.socket \
        || fail 'Could not stop user audio cleanly; nothing installed. Log out/reboot before retrying.'
fi
python3 -I - "$stage" <<'PY'
from pathlib import Path
import os
import sys
import tempfile
stage = Path(sys.argv[1])
for source in stage.rglob('*'):
    if not source.is_file():
        continue
    dest = Path('/')/source.relative_to(stage)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix='.ciri-audio-', delete=False) as f:
        tmp = Path(f.name)
        f.write(source.read_bytes())
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(source.stat().st_mode & 0o777)
    os.replace(tmp, dest)
    print(f'Installed {dest}', flush=True)
PY
systemctl daemon-reload
systemctl enable ciri-speaker-calibration.service
systemctl restart ciri-speaker-calibration.service || {
    journalctl -u ciri-speaker-calibration -n 20 --no-pager
    fail 'Calibration/safe-volume setup failed. Do not test speakers yet.'
}
# Unlike amixer, alsaucm does not match a bare card ID to hardware. The hw:
# prefix is required to resolve this ID and use the CardDriver conf.d mapping.
alsaucm -c hw:sofmt8188m98390 list _verbs \
    || fail 'UCM profile discovery failed. Files installed; do not test speakers yet.'
alsaucm -c hw:sofmt8188m98390 set _verb HiFi list _devices \
    || fail 'HiFi route validation failed. Files installed; do not test speakers yet.'
if [[ $user_session == 1 ]]; then
    user_command systemctl --user start pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service
    audio_stopped=0
    if command -v pactl >/dev/null; then
        user_command pactl set-card-profile alsa_card.platform-sound HiFi \
            || fail 'UCM validated but PipeWire did not accept HiFi. Check the user WirePlumber journal.'
    fi
else
    printf '\nNo invoking user session found. Run in your GNOME terminal, WITHOUT sudo:\n'
    printf '  systemctl --user restart pipewire pipewire-pulse wireplumber\n'
    printf '  pactl set-card-profile alsa_card.platform-sound HiFi\n'
fi
printf '\nInstalled analog-only Ciri audio and validated the HiFi controls.\n'
printf 'No playback test was run. In GNOME, select Internal Speakers and start at low volume.\n'
printf 'Check: wpctl status\n'
printf 'Factory VPD unavailable means intentionally reduced (-11 dB) speaker gain.\n'
printf 'No kernel, firmware image, account, or experimental DisplayPort changes were made.\n'
printf 'Staged installer files: %s\n' "$stage"

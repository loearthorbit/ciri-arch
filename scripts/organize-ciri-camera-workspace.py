#!/usr/bin/env -S python3 -I
"""Group retained camera work under build/camera; keep compatibility links and objects."""
import argparse
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parent.parent
REVISION = 'a5a75eeabdce349d8f794caacb3177c0e71287d5'
MOVES = {
    'ciri-camera-client-source.F6dRON1e': 'repositories/libcamera-cache',
    'ciri-camera-client-sdk.4Jej3wox': 'sdk/compiler',
    'ciri-camera-recovery.sb0PBtT8': 'recovery/chromeos-16836.19.0',
    'ciri-libcamera-sdk.HyAPSP14': 'sdk/libcamera',
    'ciri-camera-client': 'diagnostics/initial',
    'ciri-camera-client-no-gyro': 'diagnostics/no-gyro',
    'ciri-camera-client-ipa-worker': 'diagnostics/ipa-worker',
    'ciri-camera-client-no-face': 'diagnostics/no-face',
    'ciri-camera-client-raw-buffers': 'diagnostics/raw-buffers',
    'ciri-camera-client-split-planes': 'diagnostics/split-planes',
    'ciri-camera-client-colour-gains': 'diagnostics/colour-gains',
    'ciri-camera-client-warmup': 'diagnostics/warmup',
    'ciri-camera-carrier': 'kernel/carrier',
    'ciri-camera-carrier-ready': 'kernel/carrier-ready',
    'ciri-camera-companion-yjsiaoyk': 'kernel/companion',
    'ciri-camera-incremental-p43mh2bk': 'kernel/incremental',
    'ciri-camera-dtb.DNeY4H2K': 'kernel/dtb',
    'ciri-camera-logs.GsRdM7f4': 'logs/previous-capture',
    'ciri-warmup-frames': 'captures/warmup',
    'ciri-libcamera-build.log': 'logs/libcamera-build.log',
}


def organize(build, apply):
    workspace = build / 'camera'
    # Inspect all targets before the first rename. Existing unrelated paths are
    # never overwritten/deleted, and unknown directories are not guessed at.
    pending = []
    for old, relative in MOVES.items():
        source, target = build / old, workspace / relative
        if source.is_symlink():
            if source.resolve() != target.resolve() or not target.exists():
                raise ValueError(f'Unexpected existing compatibility link: {source}')
            continue
        if not source.exists():
            continue
        if target.exists() or target.is_symlink():
            raise ValueError(f'Destination exists; nothing overwritten: {target}')
        if not (source.is_dir() or source.is_file()):
            raise ValueError(f'Unsupported cache object: {source}')
        pending.append((source, target))
    for source, target in pending:
        print(f'{source.name} -> camera/{target.relative_to(workspace)} (old path remains a link)')
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)  # Same filesystem, no copy/re-extraction.
            source.symlink_to(target.relative_to(build), target_is_directory=target.is_dir())
    # Ninja contains relative source paths. Keep its physical object directory
    # in place until a separately authorized Meson reconfigure/build. Expose a
    # stable workspace link; do NOT move/wipe it and invalidate retained objects.
    objects = build / 'ciri-libcamera-meson'
    alias = workspace / 'objects/libcamera'
    if objects.exists():
        if alias.is_symlink():
            if alias.resolve() != objects.resolve():
                raise ValueError('Unexpected object workspace alias')
        elif alias.exists():
            raise ValueError('Object workspace path already exists')
        elif apply:
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.symlink_to('../../ciri-libcamera-meson', target_is_directory=True)
        print('Retain physical ciri-libcamera-meson objects; camera/objects/libcamera links to them.')
    return workspace


def source_worktree(workspace, apply):
    repository = workspace / 'repositories/libcamera-cache/libcamera'
    worktree = workspace / 'source/libcamera'
    if not repository.exists():
        if not apply:
            print('After migration: create pinned detached camera/source/libcamera worktree.')
            return
        raise ValueError('Retained source repository is missing')
    if worktree.exists():
        head = subprocess.check_output(['git', '-C', str(worktree), 'rev-parse', 'HEAD'], text=True).strip()
        if head != REVISION:
            raise ValueError('Existing source worktree has a different revision')
        print('Reuse existing pinned libcamera worktree; no reset/clean.')
        return
    print('Create pinned detached libcamera worktree; share retained Git objects, no download.')
    if not apply:
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', '-C', str(repository), 'worktree', 'add', '--detach', str(worktree), REVISION], check=True)
    subprocess.run(['git', '-C', str(worktree), 'apply', str(REPO / 'config/libcamera/ciri.patch')], check=True)
    for source, destination in (
            ('ciri_dma_buffers.h', 'include/libcamera/internal/ciri_dma_buffers.h'),
            ('ciri_gyro_sensor.cpp', 'src/libcamera/ciri_gyro_sensor.cpp')):
        shutil.copy2(REPO / 'config/libcamera' / source, worktree / destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='rename inspected caches and create worktree; default is dry-run')
    parser.add_argument('--build', type=Path, default=REPO / 'build')
    args = parser.parse_args()
    try:
        workspace = organize(args.build.resolve(), args.apply)
        source_worktree(workspace, args.apply)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Workspace organization stopped; retained files not deleted: {error}\n')
    print('No caches/backups deleted, kernel installed, source compiled, or network used.')


if __name__ == '__main__':
    main()

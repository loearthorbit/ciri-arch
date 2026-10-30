.DEFAULT_GOAL := help

PYTHON ?= python3
DOCKER_URL ?= http://127.0.0.1:23755
KERNEL_DIR ?= artifacts/kernel
FIRMWARE_DIR ?= artifacts/firmware
CGPT_DIR ?= artifacts/cgpt
SUBMARINE_KERNEL ?= artifacts/submarine/Image.gz

.PHONY: help check test cgpt kernel
help:
	@printf '%s\n' 'Ciri Arch image tooling' \
		'  make check   Syntax and unit tests; no Docker or downloads' \
		'  make test    Image construction test with real local artifacts and a nonbootable rootfs fixture' \
		'  make cgpt    Build static ARM64 cgpt on the SSH-forwarded Docker daemon' \
		'  make kernel  Build the pinned Ciri distro kernel on that daemon' \
		'  See README.md for firmware extraction and image assembly.'

check:
	$(PYTHON) -I scripts/check-project.py
	$(PYTHON) -I -m unittest discover -s tests -v

test:
	@mkdir -p build
	$(PYTHON) -I scripts/test-bundle-ciri-arch.py \
		--kernel-dir "$(KERNEL_DIR)" --firmware-dir "$(FIRMWARE_DIR)" \
		--cgpt-dir "$(CGPT_DIR)" --submarine-kernel "$(SUBMARINE_KERNEL)"

cgpt:
	$(PYTHON) -I scripts/remote-build.py --docker-url "$(DOCKER_URL)" \
		--script scripts/build-cgpt-arm64.sh --name cgpt-arm64 \
		--source-url https://chromium.googlesource.com/chromiumos/platform/vboot_reference

kernel:
	$(PYTHON) -I scripts/remote-build.py --docker-url "$(DOCKER_URL)" \
		--script scripts/build-ciri-kernel.sh --name ciri-kernel --memory-gib 6 \
		--source-url https://github.com/mkopec/linux/tree/ciri-full

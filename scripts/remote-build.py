#!/usr/bin/env python3
"""Run a reviewed build script using an SSH-forwarded Docker API and retrieve artifacts."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid


class Docker:
    def __init__(self, endpoint):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.path not in ("", "/") or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("Use a loopback HTTP endpoint reached through an SSH tunnel")
        self.endpoint = endpoint.rstrip("/")
        self.opener = build_opener(ProxyHandler({}))
        self.api = ""

    def open(self, method, path, data=None, content_type="application/json", timeout=120):
        request = Request(self.endpoint + self.api + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        return self.opener.open(request, timeout=timeout)

    def json(self, method, path, data=None, timeout=120):
        encoded = json.dumps(data).encode() if data is not None else None
        with self.open(method, path, encoded, timeout=timeout) as response:
            payload = response.read()
        return json.loads(payload) if payload else None


def verify_checksums(directory):
    lines = (directory / "SHA256SUMS").read_text().splitlines()
    if not lines:
        raise ValueError("Empty artifact checksum manifest")
    for line in lines:
        digest, name = line.split(maxsplit=1)
        if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise ValueError("Invalid artifact checksum digest")
        name = name.removeprefix("*").removeprefix("./")
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError(f"Unsafe or missing artifact checksum path: {name}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise ValueError(f"Artifact checksum mismatch: {name}")
        print(f"Verified: {name}", flush=True)


def inspect_kernel_reuse(docker, previous, image, source_url):
    """Inspect a successful retained kernel build without starting or modifying it."""
    container = (previous / "container-id.txt").read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", container):
        raise ValueError("Invalid retained container ID")
    info = docker.json("GET", f"/containers/{container}/json")
    state = info["State"]
    labels = info["Config"].get("Labels") or {}
    if state["Status"] != "exited" or state["ExitCode"] != 0:
        raise ValueError("Reuse requires a successfully exited kernel build")
    if (labels.get("purpose") != "ciri-kernel" or labels.get("source") != source_url
            or info["Config"]["Image"] != image):
        raise ValueError("Retained build source or base image differs")
    mounts = [mount for mount in info["Mounts"] if mount["Destination"] == "/work"]
    if len(mounts) != 1 or mounts[0]["Type"] != "volume" or not mounts[0].get("Name"):
        raise ValueError("Retained build must have one named /work volume")
    return {"container": container, "volume": mounts[0]["Name"], "previous_build": str(previous)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="Reviewed shell script to run inside a fresh Debian container")
    parser.add_argument("--name", default="ciri-build")
    parser.add_argument("--source-url", required=True, help="Upstream repository being built, recorded in container labels")
    parser.add_argument("--docker-url", default="http://127.0.0.1:23755")
    parser.add_argument("--image", default="debian@sha256:2dc419c6d7fe3dad0a2f9fd469c34cc90b0f9ba642d8fde837fe3d7d34a0e7f1")
    parser.add_argument("--output", help="New local directory for logs, metadata and downloaded artifacts")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--memory-gib", type=int, default=3)
    parser.add_argument("--revision", help="Optional pinned upstream commit passed as SOURCE_REVISION")
    parser.add_argument("--reuse-kernel-build", help="Previous local kernel build record; reuse its remote source, objects and installed dependencies without downloads")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", args.name):
        parser.error("--name must be a short lowercase Docker name, not a path")
    if args.jobs < 1 or args.memory_gib < 1:
        parser.error("--jobs and --memory-gib must be positive")
    if args.revision and not re.fullmatch(r"[a-fA-F0-9]{40}", args.revision):
        parser.error("--revision must be a full 40-digit upstream commit")
    previous = None
    if args.reuse_kernel_build:
        if args.name != "ciri-kernel" or Path(args.script).name != "build-ciri-kernel.sh":
            parser.error("--reuse-kernel-build is only supported by the Ciri kernel recipe")
        previous = Path(args.reuse_kernel_build).resolve(strict=True)
        if not previous.is_dir():
            parser.error("--reuse-kernel-build must name a previous build directory")
    script = Path(args.script).resolve(strict=True)
    docker = Docker(args.docker_url)
    version = docker.json("GET", "/version")
    docker.api = "/v" + version["ApiVersion"]
    print(f"Connected to Docker {version['Version']} ({version['Os']}/{version['Arch']})", flush=True)
    # Do not pull an external image implicitly.
    docker.json("GET", "/images/" + quote(args.image, safe="") + "/json")
    if args.output:
        output = Path(args.output).absolute()
        output.mkdir(parents=True, exist_ok=False)
    else:
        build = Path(__file__).resolve().parent.parent / "build"
        build.mkdir(exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix=args.name + "-", dir=build))
    tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    name = args.name + "-" + tag
    image = args.image
    reuse = None
    if previous:
        reuse = inspect_kernel_reuse(docker, previous, args.image, args.source_url)
        # Snapshot only the exited container's dependency layer, not its volumes.
        # The old source/output volume is mounted read-only in the new container.
        image = docker.json("POST", "/commit?container=" + reuse["container"] + "&pause=false")["Id"]
        reuse["dependency_image"] = image
        (output / "reuse-build.json").write_text(json.dumps(reuse, indent=2) + "\n")
        print(f"Reusing retained source/objects read-only: {reuse['volume']}; no source or dependency downloads", flush=True)
    config = {
        "Image": image,
        "Cmd": ["/bin/bash", "/opt/build.sh"],
        "Env": [f"BUILD_JOBS={args.jobs}"] + ([f"SOURCE_REVISION={args.revision}"] if args.revision else []) + (["REUSE_KERNEL_SOURCE=1"] if reuse else []),
        "Tty": True,
        "Labels": {"purpose": args.name, "source": args.source_url},
        "HostConfig": {
            "Mounts": [{"Type": "volume", "Source": name, "Target": "/work"}],
            "Memory": args.memory_gib * 1024**3,
            "NanoCpus": args.jobs * 1000000000,
            "RestartPolicy": {"Name": "no"},
        },
    }
    if reuse:
        config["HostConfig"]["Mounts"].append({
            "Type": "volume", "Source": reuse["volume"], "Target": "/source", "ReadOnly": True,
        })
    (output / "container.json").write_text(json.dumps(config, indent=2) + "\n")
    script_data = script.read_bytes()
    (output / "build-recipe.sh").write_bytes(script_data)
    container = docker.json("POST", "/containers/create?name=" + quote(name), config)["Id"]
    (output / "container-id.txt").write_text(container + "\n")
    print(f"Remote container/volume: {name}\nLocal build records: {output}", flush=True)
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        data = script_data
        member = tarfile.TarInfo("build.sh")
        member.mode = 0o755
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    with docker.open("PUT", f"/containers/{container}/archive?path=/opt", payload.getvalue(), "application/x-tar") as response:
        response.read()
    docker.json("POST", f"/containers/{container}/start")
    # Docker's wait endpoint blocks until exit; no polling or repeated build starts.
    result = docker.json("POST", f"/containers/{container}/wait?condition=not-running", timeout=7200)
    (output / "container-result.json").write_text(json.dumps(result, indent=2) + "\n")
    with docker.open("GET", f"/containers/{container}/logs?stdout=1&stderr=1") as response:
        log = response.read()
    (output / "remote-build.log").write_bytes(log)
    print("\n".join(log.decode(errors="replace").splitlines()[-60:]), flush=True)
    if result["StatusCode"] != 0 or (result.get("Error") or {}).get("Message"):
        try:
            with docker.open("GET", f"/containers/{container}/archive?path=/work/dependencies.log") as response:
                dependency_archive = response.read()
            with tarfile.open(fileobj=io.BytesIO(dependency_archive)) as archive:
                member = archive.getmember("dependencies.log")
                with archive.extractfile(member) as stream:
                    dependencies = stream.read()
            (output / "dependencies.log").write_bytes(dependencies)
            print("\n".join(dependencies.decode(errors="replace").splitlines()[-40:]), flush=True)
        except HTTPError as error:
            print(f"No dependency log available: {error}", flush=True)
        raise RuntimeError(f"Remote build failed: {result}; container retained for inspection")
    incoming = output / "download"
    incoming.mkdir()
    with docker.open("GET", f"/containers/{container}/archive?path=/work/artifacts", timeout=1800) as response:
        archive_data = response.read()
    (incoming / "artifacts.tar").write_bytes(archive_data)
    with tarfile.open(fileobj=io.BytesIO(archive_data)) as archive:
        archive.extractall(incoming, filter="data")
    artifacts = incoming / "artifacts"
    verify_checksums(artifacts)
    (output / "artifact-directory.txt").write_text(str(artifacts) + "\n")
    print(f"LOCAL ARTIFACTS: {artifacts}\nRemote container and volume retained; no host devices were mounted.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from error

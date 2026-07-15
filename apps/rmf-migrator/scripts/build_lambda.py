#!/usr/bin/env python3
"""Build the Lambda deployment package on any host OS.

Why this exists instead of a shell one-liner:

* **Cross-compilation is mandatory, not optional.** Two dependencies (`lxml`,
  `pydantic-core`) ship compiled C extensions. Building on Windows or macOS with
  a plain `pip install --target` silently produces host-native binaries
  (`.pyd` / macOS `.so`) that import fine locally and then fail *at runtime* in
  Lambda. We pin `--platform manylinux2014_x86_64 --python-version 3.12
  --implementation cp --only-binary=:all:` so the wheels always match the Lambda
  runtime, whatever machine you build on.
* **No `make` or `zip` required.** Those aren't present on a stock Windows box,
  which locked Windows contributors out of building at all.

The archive layout matches what the Terraform module's handlers expect:

    rmf_migrator/...      the package
    data/...              bundled NIST catalogs + NIST/FedRAMP baselines (no runtime fetch)
    <third-party deps>/   at the archive root

Usage:
    python scripts/build_lambda.py
    python scripts/build_lambda.py --output dist/lambda.zip --python-version 3.12
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DATA = ROOT / "data"

# Must match var.lambda_runtime in the Terraform module.
DEFAULT_PYTHON_VERSION = "3.12"
DEFAULT_PLATFORM = "manylinux2014_x86_64"


def build(output: Path, python_version: str, platform: str) -> Path:
    build_dir = BACKEND / "build"
    pkg_dir = build_dir / "package"

    if build_dir.exists():
        shutil.rmtree(build_dir)
    pkg_dir.mkdir(parents=True)

    print(
        f"installing dependencies for {platform} / cp{python_version.replace('.', '')} ..."
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            str(BACKEND),
            "--target",
            str(pkg_dir),
            "--platform",
            platform,
            "--python-version",
            python_version,
            "--implementation",
            "cp",
            # Fail loudly rather than silently falling back to a source build,
            # which would produce host-native binaries.
            "--only-binary=:all:",
            "--quiet",
        ],
        check=True,
    )

    # Guard: a host-native binary here means the cross-targeting silently failed.
    stray = [p for p in pkg_dir.rglob("*.pyd")] + [
        p for p in pkg_dir.rglob("*.so") if "x86_64-linux-gnu" not in p.name
    ]
    if stray:
        raise SystemExit(
            "refusing to build: found non-Linux compiled artifacts, which would fail "
            "at runtime in Lambda:\n  " + "\n  ".join(str(p) for p in stray[:5])
        )

    print("bundling NIST catalog data ...")
    shutil.copytree(DATA, pkg_dir / "data")

    for junk in list(pkg_dir.glob("*.dist-info")) + list(pkg_dir.glob("*.egg-info")):
        shutil.rmtree(junk, ignore_errors=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"zipping -> {output} ...")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pkg_dir.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(pkg_dir).as_posix())

    # Sanity-check the things the Lambda actually needs at runtime.
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    required = [
        "rmf_migrator/handlers/worker.py",
        "rmf_migrator/handlers/review.py",
        "data/catalogs/rev4_controls.json",
        "data/catalogs/rev5_controls.json",
        "data/mappings/rev4_to_rev5.json",
        "data/baselines/rev5_low.json",
        "data/baselines/rev5_moderate.json",
        "data/baselines/rev5_high.json",
        "data/baselines/fedramp_low.json",
        "data/baselines/fedramp_moderate.json",
        "data/baselines/fedramp_high.json",
        "data/baselines/fedramp_li_saas.json",
    ]
    missing = [r for r in required if r not in names]
    if missing:
        raise SystemExit(
            "build is missing required entries:\n  " + "\n  ".join(missing)
        )

    size_mb = output.stat().st_size / 1_000_000
    print(f"\nbuilt {output} ({size_mb:.1f} MB, {len(names)} entries)")
    if size_mb > 50:
        print(
            "WARNING: over Lambda's 50 MB direct-upload limit; "
            "publish via S3 (aws_lambda_function.s3_bucket) instead."
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND / "build" / "rmf-migrator-lambda.zip",
        help="path of the zip to write",
    )
    parser.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    args = parser.parse_args()

    build(args.output, args.python_version, args.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

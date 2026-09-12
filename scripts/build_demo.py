#!/usr/bin/env python3
"""Build the static, zero-install browser demo into dist/demo/.

The demo is the normal frontend (web/) plus the real engine: the `meddler` Python
package is zipped next to the page and run in the browser by Pyodide inside a Web
Worker (web/pyodide-worker.js). There is no server and no JavaScript port of the
simulation; the output is plain files that any static host can serve.

    python scripts/build_demo.py            # writes dist/demo/
    python -m http.server -d dist/demo 8000 # then open http://localhost:8000/

Standard library only, so CI can run it on a bare Python.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
PACKAGE_DIR = ROOT / "meddler"
DEFAULT_OUT = ROOT / "dist" / "demo"
MARKER = ".meddler-demo-build"

# Fixed timestamp so the same sources always produce the same bytes (and bundle name).
ZIP_DATE = (2020, 1, 1, 0, 0, 0)

# Meddler is AGPL-3.0-or-later: a hosted copy has to offer its Corresponding Source. The
# demo links the exact commit it was built from and ships an archive of it when git is
# available. This must be the repository the demo is published from; set
# MEDDLER_SOURCE_URL to build against a different one.
SOURCE_URL = "https://github.com/ThePrivatePanda/meddler"

ENGINE_SCRIPT = '<script src="realengine.js"></script>'
DEMO_SCRIPT = '<script src="pyodide-engine.js"></script>'
APP_SCRIPT = '<script src="app.js"></script>'


def engine_sources() -> list[Path]:
    files = [
        path
        for path in PACKAGE_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def build_bundle() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in engine_sources():
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def source_revision() -> str | None:
    """The commit this build came from: the CI-provided SHA, else the checkout's HEAD."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def source_archive(out: Path, revision: str | None) -> str | None:
    """`git archive` of that commit, served next to the page. Skipped when git can't."""
    if revision is None:
        return None
    name = f"meddler-source-{revision[:12]}.tar.gz"
    try:
        subprocess.run(
            [
                "git", "-C", str(ROOT), "archive", "--format=tar.gz",
                f"--prefix=meddler-{revision[:12]}/", "-o", str(out / name), revision,
            ],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return name


def demo_index(html: str, bundle_name: str, source: dict[str, str | None]) -> str:
    """Switch the stock page into demo mode: the in-tab engine becomes the real engine."""
    if ENGINE_SCRIPT not in html:
        raise SystemExit(f"web/index.html no longer contains {ENGINE_SCRIPT!r}; update build_demo.py")
    config = (
        "<script>window.MEDDLER_DEMO = true; "
        f"window.MEDDLER_DEMO_BUNDLE = {json.dumps(bundle_name)}; "
        f"window.MEDDLER_DEMO_SOURCE = {json.dumps(source)};</script>\n"
    )
    html = html.replace(ENGINE_SCRIPT, config + ENGINE_SCRIPT, 1)
    if DEMO_SCRIPT not in html:
        html = html.replace(ENGINE_SCRIPT, ENGINE_SCRIPT + "\n" + DEMO_SCRIPT, 1)
    if 'rel="icon"' not in html:
        # No favicon shipped yet: an empty data: icon keeps the browser from requesting a
        # missing /favicon.ico (a console 404 on every visit).
        html = html.replace("</head>", '<link rel="icon" href="data:,">\n</head>', 1)
    if APP_SCRIPT not in html:
        raise SystemExit(f"web/index.html no longer contains {APP_SCRIPT!r}; update build_demo.py")
    if not html.index(ENGINE_SCRIPT) < html.index(DEMO_SCRIPT) < html.index(APP_SCRIPT):
        raise SystemExit("pyodide-engine.js must load after realengine.js and before app.js")
    return html


def prepare_out(out: Path) -> None:
    if out.exists():
        if not (out / MARKER).is_file():
            raise SystemExit(f"refusing to overwrite {out}: it was not made by this script")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("generated by scripts/build_demo.py\n", encoding="utf-8")


def build(out: Path) -> dict[str, int]:
    prepare_out(out)
    shutil.copytree(WEB_DIR, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.md"))

    bundle = build_bundle()
    digest = hashlib.sha256(bundle).hexdigest()[:12]
    bundle_name = f"meddler-engine-{digest}.zip"
    (out / bundle_name).write_bytes(bundle)

    revision = source_revision()
    source = {
        "url": os.environ.get("MEDDLER_SOURCE_URL", SOURCE_URL),
        "commit": revision,
        "archive": source_archive(out, revision),
    }

    index = out / "index.html"
    index.write_text(
        demo_index(index.read_text(encoding="utf-8"), bundle_name, source), encoding="utf-8"
    )
    # GitHub Pages would otherwise run the site through Jekyll.
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return {
        "files": sum(1 for path in out.rglob("*") if path.is_file()),
        "bytes": sum(path.stat().st_size for path in out.rglob("*") if path.is_file()),
        "bundle": len(bundle),
        "modules": len(engine_sources()),
        "source": source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    args = parser.parse_args(argv)
    stats = build(args.out.resolve())
    source = stats["source"]
    print(
        f"demo built in {args.out}: {stats['files']} files, {stats['bytes'] / 1024:.0f} KB "
        f"(engine bundle {stats['bundle'] / 1024:.0f} KB, {stats['modules']} modules)"
    )
    print(
        f"source link: {source['url']} "
        + (f"@ {source['commit'][:12]}" if source["commit"] else "(commit unknown)")
        + (f", archive {source['archive']}" if source["archive"] else ", no archive")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

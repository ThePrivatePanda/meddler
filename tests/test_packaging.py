"""Packaging guards for pyproject.toml.

The wheel's package list is explicit (so web/ can ship as ``meddler.web`` without moving it
out of the repository root). An explicit list silently drops any new subpackage from the
wheel, so this compares it with the directories that actually exist under meddler/.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _source_packages() -> set[str]:
    found = set()
    for init in (ROOT / "meddler").rglob("__init__.py"):
        rel = init.parent.relative_to(ROOT)
        found.add(".".join(rel.parts))
    return found


def test_every_python_package_is_listed_for_the_wheel() -> None:
    listed = set(_pyproject()["tool"]["setuptools"]["packages"])
    missing = _source_packages() - listed
    assert not missing, f"add these to [tool.setuptools] packages in pyproject.toml: {missing}"


def test_web_frontend_is_mapped_into_the_wheel() -> None:
    setuptools_cfg = _pyproject()["tool"]["setuptools"]
    assert "meddler.web" in setuptools_cfg["packages"]
    assert setuptools_cfg["package-dir"]["meddler.web"] == "web"
    patterns = setuptools_cfg["package-data"]["meddler.web"]
    for name in ("index.html", "app.js", "style.css"):
        assert (ROOT / "web" / name).is_file()
        assert any(Path(name).match(p.removeprefix("**/")) for p in patterns), name

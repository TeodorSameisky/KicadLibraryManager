"""Each package only reaches downwards.

That is what the layout is for, and nothing else enforces it: one import in
the wrong direction and the domain needs a database, or the library needs a
request. This reads the imports and holds each package to what it may use.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# What each package may import from, besides itself. Top to bottom: whatever
# is not listed for a package is above it, or beside it, and not its to use.
MAY_USE = {
    "domain": set(),
    "kicad": set(),
    "errors": set(),
    "db": {"errors"},
    "library": {"domain", "errors", "kicad"},
    "auth": {"db", "domain"},
    "boms": {"db", "domain", "errors", "library"},
    "suppliers": {"db", "domain", "errors"},
    "services": {"boms", "db", "domain", "errors", "library", "suppliers"},
}


def imported(package: str) -> dict[str, set[str]]:
    """The other packages of the app each file in `package` imports, by file."""
    target = APP / package
    files = [target.with_suffix(".py")] if not target.is_dir() else sorted(target.rglob("*.py"))
    found: dict[str, set[str]] = {}
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "app":
                names = [f"app.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                parts = name.split(".")
                if parts[0] == "app" and len(parts) > 1 and parts[1] != package:
                    found.setdefault(path.relative_to(APP).as_posix(), set()).add(parts[1])
    return found


@pytest.mark.parametrize("package", sorted(MAY_USE))
def test_a_package_uses_only_what_is_below_it(package):
    reaching = {
        file: sorted(used - MAY_USE[package])
        for file, used in imported(package).items()
        if used - MAY_USE[package]
    }

    assert reaching == {}, f"{package} may use {sorted(MAY_USE[package]) or 'nothing else'}"


def test_only_the_web_knows_about_the_web():
    """Nothing below a route knows it is being asked over HTTP."""
    others = sorted(
        path.name for path in APP.iterdir() if path.name not in ("web", "application.py")
    )

    using = {
        name: sorted(files)
        for name in (other.removesuffix(".py") for other in others)
        if (files := [f for f, used in imported(name).items() if "web" in used])
    }

    assert using == {}


def test_every_package_has_its_place():
    """A new package is given one here, or it could reach anywhere."""
    packages = {
        path.name for path in APP.iterdir() if path.is_dir() and (path / "__init__.py").exists()
    }

    assert packages - set(MAY_USE) == {"web"}, "the web is on top and may use all of it"

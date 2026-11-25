"""Every third-party import is a declared dependency (LT-49).

Scans the package source for imports and checks each third-party module
against the installed package metadata, so a missing dependency fails CI
instead of only working because another package happens to pull it in.
"""

import ast
import importlib
import pkgutil
import re
import sys
from importlib.metadata import requires
from pathlib import Path

import pytest

import logtldr

SRC = Path(logtldr.__file__).parent

# Import name -> distribution name, where they differ.
DIST_NAME = {"yaml": "pyyaml"}


def _third_party_imports() -> dict[str, set[str]]:
    """{top-level module: {files importing it}} for non-stdlib, non-logtldr imports."""
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "logtldr":
                    found.setdefault(top, set()).add(path.name)
    return found


def _declared() -> set[str]:
    """Distribution names in logtldr's metadata, required or in any extra."""
    names = set()
    for req in requires("logtldr") or []:
        match = re.match(r"[A-Za-z0-9._-]+", req)
        if match:
            names.add(match.group(0).lower().replace("_", "-"))
    return names


def test_every_third_party_import_is_declared() -> None:
    declared = _declared()
    missing = {
        module: sorted(files)
        for module, files in _third_party_imports().items()
        if DIST_NAME.get(module, module).lower() not in declared
    }
    assert not missing, f"imported but not in pyproject.toml dependencies: {missing}"


def test_yaml_and_tiktoken_are_required_not_optional() -> None:
    """The two that used to arrive only through litellm are now declared directly."""
    required = {
        re.match(r"[A-Za-z0-9._-]+", r).group(0).lower()  # type: ignore[union-attr]
        for r in requires("logtldr") or []
        if "extra ==" not in r
    }
    assert {"pyyaml", "tiktoken"} <= required


@pytest.mark.parametrize(
    "module",
    sorted(
        m.name for m in pkgutil.walk_packages([str(SRC)], prefix="logtldr.")
    ),
)
def test_every_module_imports(module: str) -> None:
    """Every module in the package imports cleanly."""
    importlib.import_module(module)
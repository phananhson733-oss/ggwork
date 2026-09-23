"""The copy uv installs is the source these tests import.

backend/pyproject.toml installs ggwork-pick from backend/extensions/sources/ggwork-pick, while conftest imports
customizations/pick-workbench. Both halves are found from the repository root, so the test holds wherever it is
collected from, the managed copy's tests included.
"""

from pathlib import Path

SOURCE = Path("customizations/pick-workbench")
MANAGED = Path("backend/extensions/sources/ggwork-pick")
SHIPPED = ("ggwork_pick", "pyproject.toml")


def _repository_root() -> Path:
    here = Path(__file__).resolve()
    root = next((parent for parent in here.parents if (parent / SOURCE).is_dir() and (parent / MANAGED).is_dir()), None)
    assert root is not None, f"no directory above {here} holds both {SOURCE} and {MANAGED}"
    return root


def _shipped_files(root: Path) -> dict[str, bytes]:
    """Relative path -> bytes of every shipped file, compiled files left out."""
    files = {}
    for name in SHIPPED:
        path = root / name
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        files |= {
            str(candidate.relative_to(root)): candidate.read_bytes()
            for candidate in candidates
            if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix != ".pyc"
        }
    return files


def test_the_copy_uv_installs_is_the_source_these_tests_import():
    root = _repository_root()
    source, managed = _shipped_files(root / SOURCE), _shipped_files(root / MANAGED)
    assert "pyproject.toml" in source and "ggwork_pick/__init__.py" in source
    assert sorted(source) == sorted(managed)
    assert [name for name in sorted(source) if source[name] != managed[name]] == []

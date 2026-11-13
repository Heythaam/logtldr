"""Validate eval case structure (LT-15)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

EVAL_CASES = Path(__file__).resolve().parents[1] / "eval" / "cases"


def _case_dirs() -> list[Path]:
    return sorted(p for p in EVAL_CASES.iterdir() if p.is_dir())


def _load_gt(case_dir: Path) -> dict[str, Any]:
    loaded = yaml.safe_load((case_dir / "ground_truth.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{case_dir.name}: ground_truth.yaml is not a mapping"
    return loaded


def test_ten_cases_exist() -> None:
    dirs = _case_dirs()
    assert len(dirs) == 10, f"expected 10 cases, got {len(dirs)}"
    for i, d in enumerate(dirs, start=1):
        assert d.name.startswith(f"{i:02d}_"), f"expected prefix {i:02d}_, got {d.name}"


@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda p: p.name)
def test_case_has_required_files(case_dir: Path) -> None:
    assert (case_dir / "input.log").is_file(), f"{case_dir.name}: missing input.log"
    assert (case_dir / "ground_truth.yaml").is_file(), (
        f"{case_dir.name}: missing ground_truth.yaml"
    )


@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda p: p.name)
def test_ground_truth_fields(case_dir: Path) -> None:
    gt = _load_gt(case_dir)
    assert "status" in gt, f"{case_dir.name}: missing status"
    assert gt["status"] in ("ok", "nothing_unusual", "not_enough_signal")
    if gt["status"] == "ok":
        assert "cause" in gt, f"{case_dir.name}: ok status requires cause"
        assert "cause_keywords" in gt
        assert "cause_type" in gt
        assert gt["cause_type"] in ("direct", "temporal")


def test_at_least_one_healthy_case() -> None:
    for d in _case_dirs():
        if _load_gt(d).get("status") == "nothing_unusual":
            return
    pytest.fail("no healthy-logs case (status: nothing_unusual)")


def test_at_least_one_misleading_case() -> None:
    for d in _case_dirs():
        unknowns = _load_gt(d).get("expected_unknowns") or []
        if any(("external" in u.lower()) or ("outside" in u.lower()) for u in unknowns):
            return
    pytest.fail("no misleading case (expected_unknowns about external/outside)")
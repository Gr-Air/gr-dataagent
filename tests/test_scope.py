from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_question_module_and_database_are_owned_by_lesson() -> None:
    assert (ROOT / "data/business_analysis.sqlite").is_file()
    assert (
        ROOT
        / "integrated_agent/runtimes/question/analysis/workflows/main_flow.py"
    ).is_file()


def test_product_source_does_not_restore_removed_office_business() -> None:
    forbidden = [
        "market-budget",
        "PolicySearchCapability",
        "draft_application",
        "oa_submission",
    ]
    source_roots = [ROOT / "integrated_agent", ROOT / "static"]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for source_root in source_roots
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".md", ".html", ".js"}
    )
    assert all(token not in source for token in forbidden)

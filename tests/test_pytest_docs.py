from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_pytest_docs_include_ci_compatibility_checklist():
    text = (REPO_ROOT / "docs" / "PYTEST.md").read_text(encoding="utf-8")

    for phrase in [
        "coverage.py usage",
        "TeamCity usage",
        "pytest-randomly",
        "pytest-env",
        "pytest-sugar",
        "teamcity-messages",
        "database driver/client libraries",
        "gevent installed or active in tests",
        "should not include environment variable values",
    ]:
        assert phrase in text


def test_design_partner_preview_includes_readiness_and_safety_sections():
    text = (REPO_ROOT / "docs" / "PYTEST_DESIGN_PARTNER.md").read_text(encoding="utf-8")

    for phrase in [
        "What This Preview Is",
        "What This Preview Is Not",
        "Generated Artifacts",
        "Artifacts are local by default",
        "There is no automatic upload",
        "Do not share `recording.bin` externally",
        "Known Limitations",
        "What We Want From A Design Partner",
        "Readiness Checklist",
        "Source-tree dev E2E passes",
        "Editable-install blocker documented",
    ]:
        assert phrase in text


def test_pr_draft_lists_issue_coverage_and_limitations():
    text = (
        REPO_ROOT / "docs" / "dev" / "PYTEST_AGENT_WORKFLOW_PR_DRAFT.md"
    ).read_text(encoding="utf-8")

    for phrase in [
        "PYTEST: add failed-test recording workflow for agent inspection",
        "#24 PYTEST",
        "#25 PYTEST",
        "#26 CLI",
        "#27 CLI",
        "#28 MCP",
        "#30 SAFETY",
        "#31 PYTEST",
        "#34 CI",
        "#32 DOCS",
        "#33 DISCOVERY",
        "Known Limitations",
    ]:
        assert phrase in text

"""Every relative link in README.md and docs/*.md points at a file (and heading) that exists."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "data" / "README.md", ROOT / "webapp" / "README.md",
        *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE = re.compile(r"```.*?```", re.S)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text.replace("`", ""))
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = CODE.sub("", path.read_text(encoding="utf-8"))
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M)}


def broken_links() -> list[str]:
    broken = []
    for doc in DOCS:
        text = CODE.sub("", doc.read_text(encoding="utf-8"))
        for target in LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
                continue
            path_part, _, anchor = target.partition("#")
            dest = doc if not path_part else (doc.parent / path_part).resolve()
            if not dest.exists():
                broken.append(f"{doc.relative_to(ROOT)}: {target} (missing file)")
            elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                broken.append(f"{doc.relative_to(ROOT)}: {target} (missing heading)")
    return broken


class DocLinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        self.assertEqual(broken_links(), [])

    def test_slugs_follow_github_rules(self):
        self.assertEqual(slug("Results at a glance"), "results-at-a-glance")
        self.assertEqual(slug("What I'd do next"), "what-id-do-next")
        self.assertEqual(slug("1. Install Pantree"), "1-install-pantree")


if __name__ == "__main__":
    unittest.main()


class GeneratedBlockTests(unittest.TestCase):
    def test_readme_and_evaluation_doc_carry_every_generated_block(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("TLDR", "RESULTS", "INSIGHTS", "LEAK", "NEXT"):
            self.assertIn(f"<!-- {name}:START -->", readme, name)
        evaluation = (ROOT / "docs" / "evaluation.md").read_text(encoding="utf-8")
        for name in ("EVAL", "ABLATION"):
            self.assertIn(f"<!-- {name}:START -->", evaluation, name)

    def test_next_steps_are_built_from_the_reports(self):
        import json

        from recommendations import readme_summary

        report = json.loads(readme_summary.REPORT.read_text())
        text = readme_summary.build_next(report)
        ceiling = report["retrieval_ceiling"]
        self.assertIn(f"{ceiling['hit_rate']:.1%}", text)
        self.assertIn(f"{ceiling['candidate_list_hit_rate']:.1%}", text)
        if report.get("ablation_leave_one_source_out", {}).get("recent_activity"):
            self.assertIn("recent-activity", text)
        self.assertIn("NDCG@10", readme_summary.build_tldr(report))

"""The recommender and the storefront must not read the simulator's ground truth.

The simulator decides what customers buy from hidden inputs: each customer's true
taste (generators.taste, data/_state/customer_profiles.parquet) and a hand-written
table of complementary product types (generators.config.COMPLEMENTS). The
generator's product_affinity table is built from COMPLEMENTS too. A model that
reads any of these is being handed the answer, so only the evaluation-only
oracle module (recommendations/oracle.py) may touch the taste inputs, and
nothing may touch COMPLEMENTS or product_affinity.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("recommendations", "webapp")
ORACLE = Path("recommendations/oracle.py")

TASTE_MODULE = "generators.taste"
FORBIDDEN_CONFIG_NAMES = {"COMPLEMENTS"}
FORBIDDEN_STRINGS = {
    "customer_profiles": {ORACLE},
    "product_affinity": set(),
    "COMPLEMENTS": set(),
}


def python_files():
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            yield path.relative_to(ROOT), ast.parse(path.read_text(), filename=str(path))


def violations():
    return [v for rel, tree in python_files() for v in file_violations(rel, tree)]


def file_violations(rel, tree):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(TASTE_MODULE) and rel != ORACLE:
                    found.append(f"{rel}:{node.lineno} imports {alias.name}")
                if alias.name == "generators.config":
                    found.append(f"{rel}:{node.lineno} imports the whole generators.config")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(TASTE_MODULE) and rel != ORACLE:
                found.append(f"{rel}:{node.lineno} imports from {module}")
            if module == "generators" and any(a.name in ("taste", "config") for a in node.names):
                found.append(f"{rel}:{node.lineno} imports generators.taste/config as a module")
            if module == "generators.config":
                for alias in node.names:
                    if alias.name in FORBIDDEN_CONFIG_NAMES or alias.name == "*":
                        found.append(f"{rel}:{node.lineno} imports {alias.name} from generators.config")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for needle, allowed in FORBIDDEN_STRINGS.items():
                if needle in node.value and rel not in allowed and not _is_docstring(tree, node):
                    found.append(f"{rel}:{node.lineno} mentions {needle!r}")
    return found


def _is_docstring(tree, constant) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and body[0].value is constant:
                return True
    return False


class GroundTruthGuardTests(unittest.TestCase):
    def test_no_ground_truth_reads_outside_the_oracle(self):
        self.assertEqual(violations(), [])

    def test_guard_catches_a_forbidden_import(self):
        source = (
            "from generators.config import COMPLEMENTS\n"
            "import generators.taste\n"
            "PATH = 'data/product_affinity.parquet'\n"
            "PROFILES = 'data/_state/customer_profiles.parquet'\n"
        )
        found = file_violations(Path("recommendations/candidates/x.py"), ast.parse(source))
        self.assertEqual(len(found), 4, found)
        # The oracle may read taste inputs, but never the complement table.
        oracle_found = file_violations(ORACLE, ast.parse(source))
        self.assertEqual(len(oracle_found), 2, oracle_found)

    def test_oracle_is_the_only_taste_reader(self):
        readers = {
            str(rel)
            for rel, tree in python_files()
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(TASTE_MODULE)
        }
        self.assertEqual(readers, {str(ORACLE)})


if __name__ == "__main__":
    unittest.main()

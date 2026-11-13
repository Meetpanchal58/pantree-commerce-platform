"""The recommender and the storefront must not read the simulator's ground truth.

The simulator (the ecommerce-simulator package) decides what customers buy from hidden
inputs: each customer's true taste (ecommerce_sim.taste, data/source/_ground_truth/) and
a hand-written table of complementary product types (ecommerce_sim.reference.COMPLEMENTS).
A model that reads any of these is being handed the answer, so only the evaluation-only
oracle module (recommendations/oracle.py) may touch the taste inputs, nothing may touch
COMPLEMENTS or product_affinity, and nothing else from the package is imported.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("recommendations", "webapp")
ORACLE = Path("recommendations/oracle.py")

PACKAGE = "ecommerce_sim"
TASTE_MODULE = "ecommerce_sim.taste"
ALLOWED_IMPORTS: dict = {}  # module -> names anyone may import (none: the web app keeps its own city list)
FORBIDDEN_STRINGS = {
    "_ground_truth": {ORACLE},
    "customer_taste": {ORACLE},
    "product_quality": {ORACLE},
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
                if alias.name.startswith(TASTE_MODULE) and rel == ORACLE:
                    continue
                if alias.name == PACKAGE or alias.name.startswith(PACKAGE + "."):
                    found.append(f"{rel}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not (module == PACKAGE or module.startswith(PACKAGE + ".")):
                continue
            if module.startswith(TASTE_MODULE) and rel == ORACLE:
                continue
            allowed = ALLOWED_IMPORTS.get(module, set())
            for alias in node.names:
                if alias.name not in allowed:
                    found.append(f"{rel}:{node.lineno} imports {alias.name} from {module}")
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
            "from ecommerce_sim.reference import COMPLEMENTS\n"
            "import ecommerce_sim.taste\n"
            "PATH = 'data/source/derived/product_affinity.parquet'\n"
            "PROFILES = 'data/source/_ground_truth/customer_taste.parquet'\n"
            "from ecommerce_sim.reference import CITIES\n"
        )
        found = file_violations(Path("recommendations/candidates/x.py"), ast.parse(source))
        self.assertEqual(len(found), 6, found)  # COMPLEMENTS, taste, affinity, _ground_truth, customer_taste, CITIES
        # The oracle may read taste inputs, but never the complement table or affinity.
        oracle_found = file_violations(ORACLE, ast.parse(source))
        self.assertEqual(len(oracle_found), 3, oracle_found)  # COMPLEMENTS, affinity, CITIES

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

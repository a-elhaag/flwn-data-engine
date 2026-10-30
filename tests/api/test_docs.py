"""The generated reference docs match the code. Fix a failure with `./run.sh docs`."""

import importlib.util
import unittest
from pathlib import Path

import env  # noqa: F401  (sets the environment the app needs to import)

DOCS = Path(__file__).resolve().parents[2] / "docs"


class GeneratedDocsTests(unittest.TestCase):
    def test_committed_docs_are_current(self):
        spec = importlib.util.spec_from_file_location("docs_build", DOCS / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        for name, make in build.FILES.items():
            self.assertEqual(
                (DOCS / name).read_text(), make(), f"docs/{name} is stale: run ./run.sh docs"
            )

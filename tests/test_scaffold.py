import importlib.util
import sys
import unittest
from pathlib import Path


class ScaffoldTest(unittest.TestCase):
    def test_package_imports(self):
        import dam_outlook  # noqa: F401

    def test_requirements_installed(self):
        names = [line.split("==")[0].strip() for line in Path("requirements.txt").read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
        missing = [n for n in names if importlib.util.find_spec(n.lower().replace("-", "_")) is None]
        self.assertEqual(missing, [], f"not installed for {sys.executable}; run the tests with .venv/bin/python (AGENTS.md)")


if __name__ == "__main__":
    unittest.main()

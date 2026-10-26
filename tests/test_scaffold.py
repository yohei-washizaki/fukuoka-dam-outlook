import unittest


class ScaffoldTest(unittest.TestCase):
    def test_package_imports(self):
        import dam_outlook  # noqa: F401


if __name__ == "__main__":
    unittest.main()

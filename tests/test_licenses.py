import os
import tempfile
import unittest

from app.licenses import ALL_LICENSES, build_matrix, evaluate, normalize_license


class LicenseNormalizationTests(unittest.TestCase):
    def test_canonical_ids(self):
        for raw in ["mit", "MIT", "Mit License"]:
            lic, ok = normalize_license(raw)
            self.assertEqual(lic, "MIT")
            self.assertTrue(ok)

    def test_aliases(self):
        self.assertEqual(normalize_license("Apache 2.0")[0], "Apache-2.0")
        self.assertEqual(normalize_license("gplv3")[0], "GPL-3.0")
        self.assertEqual(normalize_license("agpl")[0], "AGPL-3.0")
        self.assertEqual(normalize_license("commercial")[0], "Proprietary")
        self.assertEqual(normalize_license("public domain")[0], "public-domain")
        self.assertEqual(normalize_license("unlicensed")[0], "Unlicense")

    def test_unknown_and_unspecified(self):
        self.assertEqual(normalize_license(None), (None, False))
        self.assertEqual(normalize_license(""), (None, False))
        self.assertEqual(normalize_license("unknown"), (None, False))
        lic, ok = normalize_license("WeirdOSL-1.0")
        self.assertFalse(ok)
        self.assertEqual(lic, "WeirdOSL-1.0")


class CompatibilityTests(unittest.TestCase):
    def test_permissive_allows_everything_common(self):
        for dep in ["MIT", "Apache-2.0", "GPL-3.0", "GPL-2.0", "Proprietary", "MPL-2.0"]:
            verdict, _ = evaluate("MIT", dep)
            self.assertEqual(verdict, "allow", f"MIT -> {dep}")

    def test_proprietary_conflicts_with_gpl_family(self):
        for dep in ["GPL-2.0", "GPL-3.0", "AGPL-3.0"]:
            verdict, reason = evaluate("Proprietary", dep)
            self.assertEqual(verdict, "conflict")
            self.assertIn(dep, reason)
        self.assertEqual(evaluate("Proprietary", "LGPL-3.0")[0], "conflict")
        self.assertEqual(evaluate("Proprietary", "MIT")[0], "allow")

    def test_apache_gpl2_only_conflict(self):
        self.assertEqual(evaluate("GPL-2.0", "Apache-2.0")[0], "conflict")
        self.assertEqual(evaluate("GPL-3.0", "Apache-2.0")[0], "allow")

    def test_gpl_version_direction(self):
        self.assertEqual(evaluate("GPL-2.0", "GPL-3.0")[0], "conflict")
        self.assertEqual(evaluate("GPL-3.0", "GPL-2.0")[0], "allow")

    def test_gpl_cannot_take_proprietary(self):
        self.assertEqual(evaluate("GPL-3.0", "Proprietary")[0], "conflict")

    def test_agpl_gates_weak_copyleft_downgrade(self):
        self.assertEqual(evaluate("AGPL-3.0", "GPL-3.0")[0], "allow")
        self.assertEqual(evaluate("AGPL-3.0", "LGPL-2.1")[0], "conflict")
        self.assertEqual(evaluate("AGPL-3.0", "MPL-2.0")[0], "allow")

    def test_unknown_pair(self):
        verdict, reason = evaluate(None, "MIT")
        self.assertEqual(verdict, "unknown")
        self.assertIn("manual review", reason)
        verdict, _ = evaluate("MIT", "WeirdOSL-1.0")
        self.assertEqual(verdict, "unknown")

    def test_matrix_is_square_and_complete(self):
        matrix = build_matrix()
        n = len(ALL_LICENSES)
        self.assertEqual(len(matrix), n)
        for consumer, row in matrix.items():
            for dep, cell in row.items():
                self.assertIn(cell["verdict"], {"allow", "conflict", "unknown"})
            self.assertEqual(len(row), n)


if __name__ == "__main__":
    unittest.main()

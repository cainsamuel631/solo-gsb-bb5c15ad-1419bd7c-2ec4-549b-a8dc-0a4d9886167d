import unittest

from app.analyzer import analyze, filter_report
from app.models import parse_manifest


def _run(payload):
    man = parse_manifest(payload)
    return man, analyze(man)


class ManifestParsingTests(unittest.TestCase):
    def test_nodes_and_edges(self):
        man = parse_manifest(
            {
                "nodes": [
                    {"name": "a", "license": "MIT"},
                    {"name": "b", "license": "GPL-3.0"},
                ],
                "edges": [{"from": "a", "to": "b"}],
            }
        )
        self.assertEqual(man.roots, ["a"])
        self.assertEqual(len(man.edges), 1)
        self.assertEqual(man.nodes["a"].license, "MIT")

    def test_packages_style_with_inline_dependency(self):
        man = parse_manifest(
            {
                "packages": [
                    {
                        "name": "a",
                        "license": "MIT",
                        "dependencies": [{"name": "b", "license": "GPL-3.0"}],
                    }
                ]
            }
        )
        self.assertIn("b", man.nodes)
        self.assertEqual(man.nodes["b"].license, "GPL-3.0")
        self.assertEqual([(e.src, e.dst) for e in man.edges], [("a", "b")])

    def test_implicit_node_from_dangling_edge(self):
        man = parse_manifest(
            {"nodes": [{"name": "a", "license": "MIT"}],
             "edges": [{"from": "a", "to": "ghost"}]}
        )
        self.assertTrue(man.nodes["ghost"].implicit)
        self.assertTrue(any("ghost" in w for w in man.warnings))

    def test_duplicate_node_version_warning(self):
        man = parse_manifest(
            {
                "nodes": [
                    {"name": "a", "version": "1.0", "license": "MIT"},
                    {"name": "a", "version": "2.0", "license": "MIT"},
                ]
            }
        )
        self.assertTrue(any("different versions" in w for w in man.warnings))
        self.assertEqual(man.nodes["a"].version, "1.0")

    def test_missing_shape_raises(self):
        from app.models import ManifestError

        with self.assertRaises(ManifestError):
            parse_manifest({"hello": "world"})
        with self.assertRaises(ManifestError):
            parse_manifest([])

    def test_unrecognized_license_warns(self):
        man = parse_manifest(
            {"nodes": [{"name": "a", "license": "X11-Proprietary"}]}
        )
        self.assertFalse(man.nodes["a"].license_recognized)
        self.assertTrue(any("not in the" in w for w in man.warnings))

    def test_inline_dependency_metadata_merges_into_package(self):
        man = parse_manifest(
            {
                "packages": [
                    {"name": "app", "license": "MIT",
                     "dependencies": [{"name": "gpl-x", "license": "GPL-3.0"}]},
                    {"name": "gpl-x"},
                ]
            }
        )
        # The later bare package declaration must not wipe the inline license.
        self.assertEqual(man.nodes["gpl-x"].license, "GPL-3.0")
        self.assertFalse(man.nodes["gpl-x"].implicit)

    def test_bare_inline_dict_reference_becomes_implicit(self):
        man = parse_manifest(
            {
                "packages": [
                    {"name": "app", "license": "MIT",
                     "dependencies": [{"name": "ghost"}]}
                ]
            }
        )
        self.assertTrue(man.nodes["ghost"].implicit)
        self.assertTrue(any("ghost" in w for w in man.warnings))


class AnalysisTests(unittest.TestCase):
    def _chain_manifest(self):
        return {
            "nodes": [
                {"name": "app", "license": "Proprietary"},
                {"name": "mid", "license": "MIT"},
                {"name": "deep", "license": "GPL-3.0"},
            ],
            "edges": [
                {"from": "app", "to": ["mid"]},
                {"from": "mid", "to": ["deep"]},
            ],
        }

    def test_transitive_conflict_has_shortest_path(self):
        _, report = _run(self._chain_manifest())
        pairs = {(f["consumer"], f["dependency"]): f for f in report["findings"]}
        self.assertIn(("app", "deep"), pairs)
        f = pairs[("app", "deep")]
        self.assertEqual(f["verdict"], "conflict")
        self.assertEqual(f["path"], ["app", "mid", "deep"])
        self.assertTrue(f["transitive"])
        self.assertEqual(f["distance"], 2)
        self.assertEqual(report["summary"]["conflicts"], 1)

    def test_direct_edge_also_reported(self):
        _, report = _run(
            {
                "nodes": [
                    {"name": "app", "license": "GPL-2.0"},
                    {"name": "apache-lib", "license": "Apache-2.0"},
                ],
                "edges": [{"from": "app", "to": "apache-lib"}],
            }
        )
        self.assertEqual(len(report["findings"]), 1)
        self.assertFalse(report["findings"][0]["transitive"])

    def test_unknown_license_marked_review(self):
        _, report = _run(
            {
                "nodes": [
                    {"name": "app", "license": "MIT"},
                    {"name": "weird", "license": "Mystery-1.0"},
                ],
                "edges": [{"from": "app", "to": "weird"}],
            }
        )
        self.assertEqual(report["summary"]["conflicts"], 0)
        self.assertEqual(report["summary"]["unknown"], 1)
        self.assertEqual(report["findings"][0]["verdict"], "unknown")

    def test_cycle_detection(self):
        _, report = _run(
            {
                "nodes": [
                    {"name": "a", "license": "MIT"},
                    {"name": "b", "license": "MIT"},
                    {"name": "c", "license": "MIT"},
                ],
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "c"},
                    {"from": "c", "to": "a"},
                ],
            }
        )
        self.assertEqual(len(report["cycles"]), 1)
        self.assertEqual(set(report["cycles"][0]), {"a", "b", "c"})
        self.assertTrue(all(n["in_cycle"] for n in report["nodes"]))

    def test_shortest_path_picks_direct_when_both_exist(self):
        # a -> b -> c (2 hops) and a -> c (1 hop): shortest must win.
        _, report = _run(
            {
                "nodes": [
                    {"name": "a", "license": "Proprietary"},
                    {"name": "b", "license": "MIT"},
                    {"name": "c", "license": "GPL-3.0"},
                ],
                "edges": [
                    {"from": "a", "to": ["b", "c"]},
                    {"from": "b", "to": ["c"]},
                ],
            }
        )
        pair = next(
            f for f in report["findings"]
            if f["consumer"] == "a" and f["dependency"] == "c"
        )
        self.assertEqual(pair["path"], ["a", "c"])
        self.assertEqual(pair["distance"], 1)

    def test_clean_manifest_has_no_findings(self):
        _, report = _run(
            {
                "nodes": [
                    {"name": "a", "license": "MIT"},
                    {"name": "b", "license": "Apache-2.0"},
                    {"name": "c", "license": "BSD-3-Clause"},
                ],
                "edges": [{"from": "a", "to": ["b", "c"]}],
            }
        )
        self.assertEqual(report["findings"], [])
        self.assertTrue(all(n["verdict"] == "allow" for n in report["nodes"]))


class FilterTests(unittest.TestCase):
    def _report(self):
        _, report = _run(
            {
                "nodes": [
                    {"name": "app", "license": "Proprietary"},
                    {"name": "mid", "license": "MIT"},
                    {"name": "gpl", "license": "GPL-3.0"},
                    {"name": "weird", "license": "Mystery-1.0"},
                ],
                "edges": [
                    {"from": "app", "to": ["mid", "weird"]},
                    {"from": "mid", "to": ["gpl"]},
                ],
            }
        )
        return report

    def test_filter_by_package_on_path(self):
        report = self._report()
        filtered = filter_report(report, package="mid")
        for f in filtered["findings"]:
            self.assertIn("mid", f["path"])

    def test_filter_by_consumer_scope(self):
        report = self._report()
        filtered = filter_report(report, package="app", scope="consumer")
        self.assertTrue(all(f["consumer"] == "app" for f in filtered["findings"]))
        self.assertGreater(len(filtered["findings"]), 0)

    def test_filter_by_dependency_scope(self):
        report = self._report()
        filtered = filter_report(report, package="gpl", scope="dependency")
        self.assertTrue(all(f["dependency"] == "gpl" for f in filtered["findings"]))
        # Only Proprietary app -> gpl conflicts; MIT mid -> gpl is allowed.
        self.assertEqual(len(filtered["findings"]), 1)

    def test_filter_severity(self):
        report = self._report()
        only_conflict = filter_report(report, severity="conflict")
        self.assertTrue(all(f["verdict"] == "conflict" for f in only_conflict["findings"]))
        only_unknown = filter_report(report, severity="unknown")
        self.assertTrue(all(f["verdict"] == "unknown" for f in only_unknown["findings"]))

    def test_subtree_scope(self):
        report = self._report()
        filtered = filter_report(report, package="mid", scope="subtree")
        consumers = {f["consumer"] for f in filtered["findings"]}
        # mid subtree contains mid itself and its dependency gpl.  mid (MIT)
        # may legally absorb gpl, so only the Proprietary ancestor app flags it.
        self.assertTrue(consumers <= {"app", "mid"})
        self.assertIn("app", consumers)
        self.assertTrue(all(f["dependency"] == "gpl" for f in filtered["findings"]))


if __name__ == "__main__":
    unittest.main()

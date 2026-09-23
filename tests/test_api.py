import json
import os
import tempfile
import unittest

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["AUDIT_DB_PATH"] = _TMP.name

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402


SAMPLE = {
    "name": "api-test",
    "nodes": [
        {"name": "app", "license": "Proprietary", "version": "1.0"},
        {"name": "lib", "license": "MIT", "version": "2.0"},
        {"name": "gpl", "license": "GPL-3.0", "version": "3.0"},
    ],
    "edges": [
        {"from": "app", "to": ["lib"]},
        {"from": "lib", "to": ["gpl"]},
    ],
}


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        cls.client = TestClient(app)

    def test_00_health_and_licenses(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        r = self.client.get("/api/licenses")
        self.assertEqual(r.status_code, 200)
        self.assertIn("matrix", r.json())
        self.assertIn("Proprietary", r.json()["matrix"])

    def test_01_index_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("许可证依赖冲突审计台", r.text)

    def test_02_create_manifest_json(self):
        r = self.client.post("/api/manifests", json=SAMPLE)
        self.assertEqual(r.status_code, 201, r.text)
        data = r.json()
        self.__class__.mid = data["id"]
        findings = data["report"]["findings"]
        pair = next(
            f for f in findings
            if f["consumer"] == "app" and f["dependency"] == "gpl"
        )
        self.assertEqual(pair["path"], ["app", "lib", "gpl"])
        self.assertEqual(pair["verdict"], "conflict")

    def test_03_create_manifest_multipart(self):
        r = self.client.post(
            "/api/manifests",
            files={"file": ("m.json", json.dumps(SAMPLE), "application/json")},
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn("report", r.json())

    def test_04_bad_json_returns_400(self):
        r = self.client.post(
            "/api/manifests",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 400)

    def test_05_bad_manifest_shape_returns_400(self):
        r = self.client.post("/api/manifests", json={"nope": 1})
        self.assertEqual(r.status_code, 400)

    def test_06_list_and_detail(self):
        r = self.client.get("/api/manifests")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["count"], 2)

        r = self.client.get(f"/api/manifests/{self.mid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "api-test")

    def test_07_filter_findings(self):
        r = self.client.get(
            f"/api/manifests/{self.mid}/findings",
            params={"package": "gpl", "scope": "dependency"},
        )
        self.assertEqual(r.status_code, 200)
        for f in r.json()["findings"]:
            self.assertEqual(f["dependency"], "gpl")

        r = self.client.get(
            f"/api/manifests/{self.mid}/findings",
            params={"severity": "conflict"},
        )
        self.assertTrue(
            all(f["verdict"] == "conflict" for f in r.json()["findings"])
        )

    def test_08_export_json(self):
        r = self.client.get(f"/api/manifests/{self.mid}/export")
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertEqual(payload["manifest_id"], self.mid)
        self.assertIn("manifest", payload)
        self.assertIn("report", payload)
        self.assertIn("findings", payload["report"])

        r = self.client.get(
            f"/api/manifests/{self.mid}/export",
            params={"package": "lib", "download": "true"},
        )
        self.assertIn("attachment", r.headers.get("content-disposition", ""))
        self.assertTrue(
            all("lib" in f["path"] for f in r.json()["report"]["findings"])
        )

    def test_09_404_for_missing(self):
        r = self.client.get("/api/manifests/999999")
        self.assertEqual(r.status_code, 404)

    def test_10_delete(self):
        created = self.client.post("/api/manifests", json=SAMPLE).json()
        rid = created["id"]
        r = self.client.delete(f"/api/manifests/{rid}")
        self.assertEqual(r.status_code, 204)
        r = self.client.get(f"/api/manifests/{rid}")
        self.assertEqual(r.status_code, 404)

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(_TMP.name)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()

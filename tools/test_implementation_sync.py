import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from check_implementation_sync import check


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.docs = Path(__file__).resolve().parents[1]
        self.manifest = json.loads(
            (self.docs / "05-技术与设计/implementation-trace.json").read_text()
        )

    def test_changed_source_rejected(self):
        with patch("check_implementation_sync.fingerprint", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "changed"):
                check(Path("."), self.docs, self.manifest)

    def test_duplicate_requirement_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["requirements"][0] = manifest["requirements"][1]
        with self.assertRaisesRegex(ValueError, "unique"):
            check(Path("."), self.docs, manifest)

    def test_reference_escape_rejected(self):
        self.manifest["requirements"][0]["code"] = "../outside"
        with self.assertRaisesRegex(ValueError, "inside"):
            check(Path("."), self.docs, self.manifest)


if __name__ == "__main__":
    unittest.main()

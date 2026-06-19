from __future__ import annotations

import csv
import gzip
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_api.py"
SPEC = importlib.util.spec_from_file_location("build_api", SCRIPT_PATH)
build_api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_api
SPEC.loader.exec_module(build_api)
MOSCOW = ZoneInfo("Europe/Moscow")


class BuildApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mapping = self.root / "mapping.csv"
        with self.mapping.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    "rapidnas_id",
                    "rapidnas_name",
                    "iptv_org_id",
                    "iptv_org_name",
                    "programme_count",
                    "status",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "rapidnas_id": "rapid-1",
                    "rapidnas_name": "Test Channel",
                    "iptv_org_id": "Test.ru",
                    "iptv_org_name": "Test",
                    "programme_count": "1",
                    "status": "verified",
                }
            )
        self.source = self.root / "source.xml.gz"
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<tv><channel id=\"rapid-1\"><display-name>Test</display-name></channel>"
            "<programme start=\"20260619090000 +0300\" "
            "stop=\"20260619100000 +0300\" channel=\"rapid-1\">"
            "<title>Morning show</title><category>News</category></programme></tv>"
        )
        with gzip.open(self.source, "wb") as output:
            output.write(xml.encode("utf-8"))
        self.original_limits = (
            build_api.MIN_XML_CHANNELS,
            build_api.MIN_XML_PROGRAMMES,
        )
        build_api.MIN_XML_CHANNELS = 1
        build_api.MIN_XML_PROGRAMMES = 1

    def tearDown(self):
        (
            build_api.MIN_XML_CHANNELS,
            build_api.MIN_XML_PROGRAMMES,
        ) = self.original_limits
        self.temporary.cleanup()

    def test_build_writes_channel_and_status(self):
        public = self.root / "public"
        now = datetime(2026, 6, 19, 8, 0, tzinfo=MOSCOW)
        status = build_api.build(self.mapping, self.source, public, 2, now)
        channel = json.loads(
            (public / "v1" / "channels" / "Test.ru.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(status["publishedChannels"], 1)
        self.assertEqual(channel["programmeCount"], 1)
        self.assertEqual(channel["days"][0]["programs"][0]["title"], "Morning show")

    def test_recent_publication_is_detected(self):
        public = self.root / "public"
        status = public / "v1" / "status.json"
        status.parent.mkdir(parents=True)
        status.write_text(
            json.dumps({"generatedAt": "2026-06-19T08:00:00+03:00"}),
            encoding="utf-8",
        )
        now = datetime(2026, 6, 19, 20, 0, tzinfo=MOSCOW)
        self.assertTrue(build_api.is_recent(public, 18, now))

    def test_best_duplicate_channel_source_is_selected(self):
        with self.mapping.open("a", encoding="utf-8", newline="") as output:
            output.write("rapid-2,Test 2,Test.ru,Test,20,verified\n")
        mappings = build_api.load_mappings(self.mapping)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0].rapidnas_id, "rapid-2")


if __name__ == "__main__":
    unittest.main()

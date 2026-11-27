import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from analyzer import report
from storage import Store


class ReportTrendTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "traffic.db")
        self.store.initialize()
        self.zone = ZoneInfo("Asia/Shanghai")
        self.midnight = datetime(2026, 11, 27, tzinfo=self.zone)
        self.now = (self.midnight + timedelta(hours=23, minutes=59)).timestamp()

    def add_traffic(self, when, route, upload, download, sampled=True):
        timestamp = int(when.timestamp())
        with self.store.connect() as db:
            db.execute(
                """INSERT INTO traffic_stats (
                    bucket_ts, root_domain, host, process, rule_type, proxy_group,
                    route_type, up_bytes, down_bytes, conn_count
                ) VALUES (?, 'example.com', 'example.com', '', 'MATCH', '', ?, ?, ?, 1)""",
                (timestamp, route, upload, download),
            )
            if sampled:
                db.execute("INSERT OR IGNORE INTO sample_buckets VALUES (?, 1)", (timestamp,))

    def test_today_groups_hours_and_preserves_daily_totals(self):
        self.add_traffic(self.midnight - timedelta(minutes=5), "PROXY", 1000, 1000)
        self.add_traffic(self.midnight, "PROXY", 10, 20)
        self.add_traffic(self.midnight + timedelta(minutes=55), "PROXY", 5, 15)
        self.add_traffic(self.midnight + timedelta(hours=1), "DIRECT", 30, 40)
        self.add_traffic(self.midnight + timedelta(hours=23, minutes=55), "UNKNOWN", 2, 3)
        self.add_traffic(self.midnight + timedelta(days=1), "PROXY", 1000, 1000)

        result = report(self.store, 1, self.zone, False, now=self.now)
        self.assertEqual(result["trend_grain"], "hour")
        self.assertEqual(len(result["trend"]), 24)
        self.assertEqual(result["trend"][0]["traffic"], {"PROXY": 50})
        self.assertEqual(result["trend"][0]["sample_buckets"], 2)
        self.assertEqual(result["trend"][1]["traffic"], {"DIRECT": 70})
        self.assertEqual(result["trend"][2]["traffic"], {})
        self.assertEqual(result["trend"][2]["sample_buckets"], 0)
        self.assertEqual(result["trend"][-1]["traffic"], {"UNKNOWN": 5})
        self.assertEqual(result["trend"][0]["start_ts"], int(self.midnight.timestamp()))
        self.assertEqual(result["trend"][-1]["end_ts"], int((self.midnight + timedelta(days=1)).timestamp()))
        for route, total in result["totals"].items():
            self.assertEqual(sum(point["traffic"].get(route, 0) for point in result["trend"]), total)

    def test_empty_today_still_has_full_timeline(self):
        result = report(self.store, 1, self.zone, False, now=self.now)
        self.assertEqual(len(result["trend"]), 24)
        self.assertTrue(all(not point["traffic"] and point["sample_buckets"] == 0 for point in result["trend"]))

    def test_longer_periods_remain_daily(self):
        self.add_traffic(self.midnight, "PROXY", 10, 20)
        for days in [7, 14, 30]:
            with self.subTest(days=days):
                result = report(self.store, days, self.zone, False, now=self.now)
                self.assertEqual(result["trend_grain"], "day")
                self.assertEqual(len(result["trend"]), days)
                self.assertEqual(result["trend"][-1]["traffic"], {"PROXY": 30})

    def test_local_midnights_and_dst_boundaries(self):
        for zone_name, day, hours in [
            ("Asia/Kathmandu", (2026, 11, 27), 24),
            ("America/New_York", (2026, 3, 8), 23),
            ("America/New_York", (2026, 11, 1), 25),
        ]:
            with self.subTest(zone=zone_name, day=day):
                zone = ZoneInfo(zone_name)
                start = datetime(*day, tzinfo=zone)
                end = start + timedelta(days=1)
                result = report(self.store, 1, zone, False, now=(end - timedelta(seconds=1)).timestamp())
                self.assertEqual(len(result["trend"]), hours)
                self.assertEqual(result["trend"][0]["start_ts"], int(start.timestamp()))
                self.assertEqual(result["trend"][-1]["end_ts"], int(end.timestamp()))


if __name__ == "__main__":
    unittest.main()

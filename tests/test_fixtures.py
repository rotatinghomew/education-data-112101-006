import csv
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class FixtureIntegrityTests(unittest.TestCase):
    def test_fixture_csv_files_are_readable_and_have_rows(self):
        files = sorted((ROOT / "data").glob("*.csv"))
        self.assertGreaterEqual(len(files), 3)
        for path in files:
            with self.subTest(file=path.name), path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertTrue(reader.fieldnames)
                rows = list(reader)
                self.assertTrue(rows)
                self.assertTrue(all(set(row) == set(reader.fieldnames) for row in rows))

    def test_orders_reference_known_sales_sessions(self):
        def read_rows(name):
            with (ROOT / "data" / name).open(encoding="utf-8", newline="") as stream:
                return list(csv.DictReader(stream))

        sessions = {row["session_ref"] for row in read_rows("sessions.csv")}
        orders = read_rows("orders.csv")
        self.assertTrue(sessions)
        self.assertTrue(all(row["session_ref"] in sessions for row in orders))
        self.assertTrue(all(row["sales_method"] for row in read_rows("sessions.csv")))

if __name__ == "__main__":
    unittest.main()

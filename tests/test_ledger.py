import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger import (  # noqa: E402
    build_report,
    cash_events,
    classify_order,
    explain,
    load_dataset,
    pending_refunds,
    refund_state_as_of,
    signing_contribution,
)
from ledger.views import NEW, RENEWAL, UPGRADE  # noqa: E402

DATA_DIR = ROOT / "data"


class SalesViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(DATA_DIR)
        cls.report = build_report(cls.ds)

    def test_order_classification_counts(self):
        classes = [
            classify_order(o, self.ds.orders_by_ref) for o in self.ds.orders
        ]
        self.assertEqual(classes.count(NEW), 24)
        self.assertEqual(classes.count(UPGRADE), 8)
        self.assertEqual(classes.count(RENEWAL), 1)

    def test_upgrade_chain_not_double_counted(self):
        orders = self.ds.orders_by_ref
        # O-1(6000) 升级为 O-2(9000)：链上新签合计应为 9000，而非 15000
        self.assertEqual(signing_contribution(orders["O-1"], orders), 6000)
        self.assertEqual(signing_contribution(orders["O-2"], orders), 3000)
        naive_total = sum(o.gross_amount for o in self.ds.orders)
        self.assertEqual(naive_total, 138000)
        dedup_total = sum(
            signing_contribution(o, orders) for o in self.ds.orders
        )
        self.assertEqual(dedup_total, 103440)

    def test_sales_monthly_spot_check(self):
        monthly = {row["period"]: row for row in self.report["sales"]["monthly"]}
        jan = monthly["2026-01"]
        self.assertEqual(jan["new"]["count"], 4)
        self.assertEqual(jan["new"]["amount"], 17880)
        self.assertEqual(jan["upgrade"]["count"], 2)
        self.assertEqual(jan["upgrade"]["amount"], 240)
        self.assertEqual(jan["signed_total"], 18120)

    def test_sales_quarterly_rollup(self):
        quarterly = {row["period"]: row for row in self.report["sales"]["quarterly"]}
        self.assertEqual(quarterly["2026Q1"]["signed_total"], 52680)
        self.assertEqual(quarterly["2026Q2"]["signed_total"], 50760)

    def test_sales_by_method_covers_all_orders(self):
        by_method = self.report["sales"]["by_method"]
        total = sum(row["signed_total"] for row in by_method)
        self.assertEqual(total, 103440)
        methods = {row["method"] for row in by_method}
        self.assertEqual(
            methods, {"低价体验后续单", "直播间限时续单", "课程说明页直接购买"}
        )


class CashViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(DATA_DIR)
        cls.report = build_report(cls.ds)

    def test_cash_totals(self):
        events = cash_events(self.ds)
        receipts = sum(e.amount for e in events if e.kind == "payment")
        reversals = sum(-e.amount for e in events if e.kind == "reversal")
        refunds = sum(-e.amount for e in events if e.kind == "refund")
        self.assertEqual(receipts, 35700)
        self.assertEqual(reversals, 33600)
        self.assertEqual(refunds, 10800)
        self.assertEqual(receipts - reversals - refunds, -8700)

    def test_reversal_booked_in_its_own_period(self):
        # P-1 一月收款 3000，P-2 二月冲正：两个期间分别体现
        monthly = {row["period"]: row for row in self.report["cash"]["monthly"]}
        self.assertEqual(monthly["2026-01"]["receipts"], 12900)
        self.assertEqual(monthly["2026-02"]["receipts"], 3000)
        self.assertEqual(monthly["2026-02"]["reversals"], 13200)
        p2 = [e for e in cash_events(self.ds) if e.source_ref == "P-2"][0]
        self.assertEqual(p2.kind, "reversal")
        self.assertEqual(p2.amount, -3000)
        self.assertEqual(p2.occurred_at, date(2026, 2, 1))
        self.assertEqual(p2.linked_ref, "P-1")

    def test_pending_refunds_not_in_cash(self):
        events = cash_events(self.ds)
        self.assertFalse(any(e.source_ref == "R-2" for e in events))
        pending = {r.refund_ref: r for r in pending_refunds(self.ds)}
        self.assertIn("R-2", pending)
        self.assertEqual(sum(r.amount for r in pending.values()), 8300)

    def test_disputed_listed_separately(self):
        outstanding = self.report["outstanding"]
        disputed_refs = {row["order_ref"] for row in outstanding["disputed_orders"]}
        self.assertIn("O-3", disputed_refs)
        self.assertEqual(outstanding["disputed_gross_total"], 42600)

    def test_cash_by_method_reconciles_with_monthly(self):
        by_method = self.report["cash"]["by_method"]
        monthly = self.report["cash"]["monthly"]
        for key in ("receipts", "reversals", "refunds", "net"):
            self.assertEqual(
                sum(row[key] for row in by_method),
                sum(row[key] for row in monthly),
                msg=key,
            )


class AsOfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(DATA_DIR)

    def test_refund_state_reconstruction(self):
        refund = next(r for r in self.ds.refunds if r.refund_ref == "R-1")
        # R-1 申请于 02-08、结算于 03-02
        self.assertEqual(refund_state_as_of(refund, date(2026, 1, 31)), None)
        self.assertEqual(refund_state_as_of(refund, date(2026, 2, 15)), "pending")
        self.assertEqual(refund_state_as_of(refund, date(2026, 3, 31)), "settled")

    def test_as_of_report_excludes_future_settlement(self):
        report = build_report(self.ds, as_of=date(2026, 2, 15))
        cash_refs = [
            e.source_ref for e in cash_events(self.ds, date(2026, 2, 15))
        ]
        self.assertNotIn("R-1", cash_refs)
        pending_refs = {
            row["refund_ref"] for row in report["outstanding"]["pending_refunds"]
        }
        self.assertIn("R-1", pending_refs)
        self.assertNotIn("R-2", pending_refs)  # 03-01 才申请，当时尚不可见

    def test_as_of_report_excludes_future_orders(self):
        report = build_report(self.ds, as_of=date(2026, 3, 31))
        periods = [row["period"] for row in report["sales"]["monthly"]]
        self.assertNotIn("2026-04", periods)

    def test_published_report_reproducible(self):
        # 同一口径日期重跑，结果一致（已发布复盘可按当时状态还原）
        first = build_report(self.ds, as_of=date(2026, 3, 31))
        second = build_report(self.ds, as_of=date(2026, 3, 31))
        self.assertEqual(first, second)


class FulfillmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(DATA_DIR)
        cls.report = build_report(cls.ds)

    def test_partial_refund_keeps_service_units(self):
        lines = {
            line["order_ref"]: line for line in self.report["fulfillment"]["lines"]
        }
        # O-4 有已结算部分退款 R-4(200)，服务单位仍按课程版本计入
        self.assertEqual(lines["O-4"]["units"], 8)
        # O-6 下单日对应 K-1 版本 1（12 单位）
        self.assertEqual(lines["O-6"]["units"], 12)

    def test_upgraded_parent_represented_by_child(self):
        lines = self.report["fulfillment"]["lines"]
        refs = {line["order_ref"] for line in lines}
        self.assertNotIn("O-1", refs)  # 已被 O-2 取代
        self.assertIn("O-2", refs)


class ExplainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(DATA_DIR)
        cls.result = explain(cls.ds, date(2026, 2, 15), date(2026, 3, 31))

    def test_refund_settlement_explained(self):
        by_ref = {c["ref"]: c for c in self.result["changes"]}
        self.assertEqual(by_ref["R-1"]["kind"], "refund_settled")
        self.assertEqual(by_ref["R-1"]["cash_impact"], -1200)
        self.assertEqual(by_ref["R-1"]["period"], "2026-03")
        self.assertEqual(by_ref["R-2"]["kind"], "refund_requested")
        self.assertEqual(by_ref["R-2"]["cash_impact"], 0)

    def test_period_deltas(self):
        self.assertEqual(
            self.result["cash_delta_by_period"],
            {"2026-02": -2040, "2026-03": 6300},
        )
        self.assertEqual(self.result["sales_delta_by_period"]["2026-03"], 8760)

    def test_every_change_names_source_transaction(self):
        for change in self.result["changes"]:
            self.assertIn(change["ref"], change["description"])


if __name__ == "__main__":
    unittest.main()

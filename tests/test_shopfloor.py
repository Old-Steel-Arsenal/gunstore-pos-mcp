"""Shop-floor tools: stocktake, cash drawer, storage locations (tools/shopfloor.py).

Fully mocked, like the rest of the suite: they pin the dotted method path and the
kwargs of every tool, the confirm gate on every write, the argument validation, and
that every write is ALSO gated for the bare `frappe_run_method` path. Signatures were
checked against gunstore-pos origin/develop (merge 46eb5195) when written; a drift
there would not show up here.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from gunstore_mcp import modes
from gunstore_mcp.frappe_client import FrappeClient
from gunstore_mcp.safety import WriteRefused
from gunstore_mcp.tools import generic, shopfloor

IC = "ffl_core.api.inventory_count."
CD = "ffl_core.api.cash_drawer."
ST = "ffl_core.api.storage."

# tool -> (kwargs that make a valid call, dotted method(s) it may reach)
WRITES = {
    "inventory_count_create": ({}, IC + "create_count"),
    "inventory_count_scan": ({"count": "C1", "code": "SN1"}, IC + "scan"),
    "inventory_count_set_qty": (
        {"count": "C1", "item_code": "I", "warehouse": "W", "qty": 3}, IC + "set_qty"),
    "inventory_count_toggle_serial": (
        {"count": "C1", "serial": "SN1", "found": True}, IC + "toggle_serial"),
    "inventory_count_undo": ({"entry": "E1"}, IC + "undo"),
    "inventory_count_cancel": ({"count": "C1"}, IC + "cancel_count"),
    "inventory_count_finalize": ({"count": "C1"}, IC + "finalize"),
    "cash_drawer_close_day": ({"counted": 100.0}, CD + "close_day"),
    "cash_drawer_record_entry": ({"kind": "deposit", "amount": 50}, CD + "record_deposit"),
    "cash_drawer_undo": ({"entry": "CDE-1"}, CD + "undo_entry"),
    "cash_drawer_record_payout": (
        {"acquisition": "ACQ-1", "method": "Zelle"}, CD + "record_payout"),
    "storage_create_zone": ({"zone_name": "A", "kind": "Slots", "count": 4}, ST + "create_zone"),
    "storage_add_positions": ({"zone": "A", "count": 2}, ST + "add_positions"),
    "storage_set_disabled": ({"disabled": True, "zone": "A"}, ST + "set_disabled"),
    "storage_scan_move": ({"to_location": "A1", "code": "SN1"}, ST + "scan_move"),
    "storage_undo_move": ({"move": "SM-1"}, ST + "undo_move"),
    "storage_confirm_taken": (
        {"item_code": "I", "location": "A1", "qty": 2}, ST + "confirm_taken"),
}


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeClient:
    def __init__(self):
        self.calls = []

    def call_method(self, method, kwargs=None):
        self.calls.append(("call_method", method, kwargs or {}))
        return {"ok": True}

    def run_report(self, name, filters=None):
        self.calls.append(("run_report", name, filters))
        return {"ok": True}

    def list_documents(self, doctype, **kw):
        self.calls.append(("list_documents", doctype, kw))
        return [{"name": "ROW-1"}]

    def get_document(self, doctype, name):
        self.calls.append(("get_document", doctype, name))
        return {"default_company": "Old Steel Arsenal Inc"}


class ShopfloorBase(unittest.TestCase):
    def setUp(self):
        mcp = FakeMCP()
        shopfloor.register(mcp)
        self.tools = mcp.tools
        self.client = FakeClient()
        for mod in (shopfloor,):
            p = patch.object(mod, "get_client", return_value=self.client)
            p.start()
            self.addCleanup(p.stop)
        # _default_company lives in reports.py and takes the client it is handed
        self.tool = lambda name: self.tools[name]


class Surface(ShopfloorBase):
    def test_tool_count_pinned(self):
        # 13 reads + 17 writes. TOOLS.md / CLAUDE.md / README quote the TOTAL;
        # tests/test_doc_counts.py is the gate that says where.
        self.assertEqual(len(self.tools), 30)
        self.assertEqual(len(WRITES), 17)

    def test_every_write_is_listed_here_and_nothing_else_is(self):
        reads = set(self.tools) - set(WRITES)
        self.assertEqual(reads, {
            "inventory_counts", "inventory_count_state", "inventory_count_variance",
            "cash_drawer_today", "cash_drawer_closes", "cash_drawer_entries", "cash_drawer_log",
            "cash_drawer_weekly", "cash_drawer_preview_close",
            "storage_map", "storage_location", "storage_where", "storage_unassigned",
        })

    def test_no_tool_registration_gate(self):
        """Owner decision: registered by default, confirm on every write. A gate
        env var would have to extend test_doc_counts._count()/_live(); there is none."""
        self.assertFalse([n for n in dir(shopfloor) if n.endswith("_ENV")])


class ConfirmGate(ShopfloorBase):
    def test_every_write_refuses_without_confirm_and_never_calls_the_pos(self):
        for name, (kwargs, _method) in WRITES.items():
            with self.subTest(tool=name):
                with self.assertRaises(WriteRefused):
                    self.tool(name)(**kwargs)
                with self.assertRaises(WriteRefused):
                    self.tool(name)(**kwargs, confirm=False)
        self.assertEqual(self.client.calls, [])

    def test_every_write_reaches_its_method_once_confirmed(self):
        for name, (kwargs, method) in WRITES.items():
            with self.subTest(tool=name):
                self.client.calls.clear()
                self.tool(name)(**kwargs, confirm=True)
                self.assertEqual([c[1] for c in self.client.calls], [method])

    def test_every_write_method_is_gated_for_the_bare_run_method_path_too(self):
        """frappe_run_method reaches these dotted names directly. Each must be on
        _ALWAYS_CONFIRM_METHODS or match the destructive-verb regex, else the confirm
        on the dedicated tool is a door with a window next to it."""
        for name, (_kwargs, method) in WRITES.items():
            with self.subTest(method=method):
                gated = (method in generic._ALWAYS_CONFIRM_METHODS
                         or generic._DESTRUCTIVE_METHOD.search(method.rsplit(".", 1)[-1]))
                self.assertTrue(gated, f"{method} is reachable ungated via frappe_run_method")

    def test_record_entry_methods_are_all_gated(self):
        for method in ("record_deposit", "record_from_bank", "record_expense", "record_owner_draw"):
            self.assertIn(CD + method, generic._ALWAYS_CONFIRM_METHODS)
        for method in ("undo_entry", "undo_close"):
            self.assertIn(CD + method, generic._ALWAYS_CONFIRM_METHODS)

    def test_floor_plan_writes_are_gated(self):
        for method in ("create_floor_plan", "save_floor_plan"):
            self.assertIn(ST + method, generic._ALWAYS_CONFIRM_METHODS)
        self.assertTrue(generic._DESTRUCTIVE_METHOD.search("delete_floor_plan"))

    def test_trade_in_and_cost_correction_book_cash_so_they_are_gated(self):
        self.assertIn("ffl_core.api.trade_in.create_trade_in_intake",
                      generic._ALWAYS_CONFIRM_METHODS)
        self.assertIn("ffl_core.api.cost_correction.correct_serial_cost",
                      generic._ALWAYS_CONFIRM_METHODS)

    def test_reads_need_no_confirm(self):
        self.tool("inventory_counts")()
        self.tool("cash_drawer_today")()
        self.tool("cash_drawer_preview_close")(counted=10)
        self.tool("storage_map")()
        self.assertEqual(len(self.client.calls), 4)


class InventoryCount(ShopfloorBase):
    def test_reads(self):
        self.tool("inventory_counts")()
        self.tool("inventory_count_state")("C1")
        self.tool("inventory_count_state")("C1", counts_only=True)
        self.tool("inventory_count_variance")("C1")
        self.assertEqual(self.client.calls, [
            ("call_method", IC + "get_counts", {}),
            ("call_method", IC + "get_state", {"count": "C1", "counts_only": 0}),
            ("call_method", IC + "get_state", {"count": "C1", "counts_only": 1}),
            ("call_method", IC + "variance", {"count": "C1"}),
        ])

    def test_create_kwargs(self):
        self.tool("inventory_count_create")(
            warehouses=["Stores - X"], item_groups=["Ammo"], include_custody=False,
            notes="n", confirm=True)
        self.assertEqual(self.client.calls[0][2], {
            "company": None, "warehouses": ["Stores - X"], "item_groups": ["Ammo"],
            "include_custody": 0, "notes": "n"})

    def test_scan_and_entries_kwargs(self):
        self.tool("inventory_count_scan")("C1", "0123", warehouse="W", confirm=True)
        self.tool("inventory_count_set_qty")("C1", "I", "W", 0, confirm=True)
        self.tool("inventory_count_toggle_serial")("C1", "SN1", False, confirm=True)
        self.tool("inventory_count_undo")("E1", confirm=True)
        self.tool("inventory_count_cancel")("C1", confirm=True)
        self.assertEqual([c[2] for c in self.client.calls], [
            {"count": "C1", "code": "0123", "warehouse": "W"},
            {"count": "C1", "item_code": "I", "warehouse": "W", "qty": 0},
            {"count": "C1", "serial": "SN1", "found": 0},
            {"entry": "E1"},
            {"count": "C1"},
        ])

    def test_finalize_passes_the_chosen_rows_and_defaults_to_none(self):
        rows = [{"item_code": "I", "warehouse": "W"}]
        self.tool("inventory_count_finalize")("C1", item_rows=rows, confirm=True)
        self.tool("inventory_count_finalize")("C1", confirm=True)
        self.assertEqual(self.client.calls[0][2], {"count": "C1", "item_rows": rows})
        # None = "every row with a difference except never-scanned" on the server
        self.assertEqual(self.client.calls[1][2], {"count": "C1", "item_rows": None})

    def test_finalize_docstring_says_it_posts_stock_and_spares_firearms(self):
        doc = self.tool("inventory_count_finalize").__doc__
        self.assertIn("STOCK RECONCILIATION", doc)
        self.assertIn("NEVER adjusted", doc)


class CashDrawer(ShopfloorBase):
    def test_today_and_preview(self):
        self.tool("cash_drawer_today")("OSA")
        self.tool("cash_drawer_preview_close")(123.45, "OSA")
        self.assertEqual(self.client.calls, [
            ("call_method", CD + "get_today", {"company": "OSA"}),
            ("call_method", CD + "preview_close", {"company": "OSA", "counted": 123.45}),
        ])

    def test_close_day_kwargs(self):
        self.tool("cash_drawer_close_day")(
            100.0, "OSA", reason="short", expected_seen=110.0, confirm=True)
        self.assertEqual(self.client.calls[0], ("call_method", CD + "close_day", {
            "company": "OSA", "counted": 100.0, "reason": "short", "expected_seen": 110.0}))

    def test_closes_filters_and_default_company(self):
        self.tool("cash_drawer_closes")(from_date="2026-10-01", to_date="2026-10-07")
        get, lst = self.client.calls
        self.assertEqual(get[:2], ("get_document", "Global Defaults"))
        kind, doctype, kw = lst
        self.assertEqual((kind, doctype), ("list_documents", "Cash Drawer Close"))
        self.assertEqual(kw["filters"], [
            ["company", "=", "Old Steel Arsenal Inc"], ["status", "!=", "Undone"],
            ["business_date", ">=", "2026-10-01"], ["business_date", "<=", "2026-10-07"]])
        self.assertIn("variance", kw["fields"])
        self.assertEqual(kw["limit"], 30)

    def test_closes_can_include_undone_and_caps_the_page(self):
        self.tool("cash_drawer_closes")("OSA", include_undone=True, limit=10_000)
        _, _, kw = self.client.calls[0]
        self.assertEqual(kw["filters"], [["company", "=", "OSA"]])
        self.assertEqual(kw["limit"], 500)

    def test_entries_filters(self):
        self.tool("cash_drawer_entries")(
            "OSA", from_date="2026-10-01", entry_type="Expense", status=None)
        _, doctype, kw = self.client.calls[0]
        self.assertEqual(doctype, "Cash Drawer Entry")
        self.assertEqual(kw["filters"], [
            ["company", "=", "OSA"], ["entry_type", "=", "Expense"],
            ["posting_date", ">=", "2026-10-01"]])
        self.assertIn("receipt", kw["fields"])
        self.tool("cash_drawer_entries")("OSA")
        self.assertIn(["status", "=", "Posted"], self.client.calls[1][2]["filters"])

    def test_log(self):
        self.tool("cash_drawer_log")("2026-09-28", "2026-10-04", company="OSA")
        self.assertEqual(self.client.calls[0], ("call_method", CD + "get_log", {
            "company": "OSA", "from_date": "2026-09-28", "to_date": "2026-10-04"}))

    def test_log_defaults_to_the_same_company_as_the_other_reads(self):
        self.tool("cash_drawer_log")()
        self.assertEqual(self.client.calls[-1][0:2], ("call_method", CD + "get_log"))
        self.assertTrue(self.client.calls[-1][2]["company"])  # resolved here, like closes / entries

    def test_weekly_report(self):
        self.tool("cash_drawer_weekly")("2026-09-28", "2026-10-04", company="OSA")
        self.assertEqual(self.client.calls[0], ("run_report", "Cash Drawer Weekly", {
            "company": "OSA", "from_date": "2026-09-28", "to_date": "2026-10-04"}))

    def test_record_entry_deposit_from_bank_expense(self):
        f = self.tool("cash_drawer_record_entry")
        f("deposit", 500, "OSA", reference="slip 9", confirm=True)
        f("from_bank", 200, reference="W-1", confirm=True)
        f("expense", 12.5, expense_account="Postal - X", memo="stamps",
          receipt="/private/files/r.jpg", confirm=True)
        f("expense", 9, expense_account="Meals - X", memo="lunch", confirm=True)  # receipt optional
        self.assertEqual(self.client.calls, [
            ("call_method", CD + "record_deposit",
             {"company": "OSA", "amount": 500, "reference": "slip 9"}),
            ("call_method", CD + "record_from_bank",
             {"company": None, "amount": 200, "reference": "W-1"}),
            ("call_method", CD + "record_expense",
             {"company": None, "amount": 12.5, "expense_account": "Postal - X",
              "memo": "stamps", "receipt": "/private/files/r.jpg"}),
            ("call_method", CD + "record_expense",
             {"company": None, "amount": 9, "expense_account": "Meals - X",
              "memo": "lunch", "receipt": None}),
        ])

    def test_record_entry_refuses_bad_shapes_before_any_call(self):
        f = self.tool("cash_drawer_record_entry")
        bad = [
            dict(kind="refund", amount=1),                                  # unknown kind
            dict(kind="deposit", amount=1, memo="x"),                       # stray arg
            dict(kind="deposit", amount=1, receipt="/private/files/r.jpg"),  # stray arg
            dict(kind="owner_draw", amount=1),                              # gone
            dict(kind="from_bank", amount=1, memo="m"),                     # stray arg
            dict(kind="expense", amount=1, expense_account="A"),             # no memo
            dict(kind="expense", amount=1, memo="  ", expense_account="A", receipt="/r"),  # blank memo
            dict(kind="expense", amount=1, memo="m", receipt="/r"),          # no account
        ]
        for kw in bad:
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    f(**kw, confirm=True)
        self.assertEqual(self.client.calls, [])

    def test_kind_is_validated_even_without_confirm(self):
        # a malformed call should say so, not ask for a confirm it can never use
        with self.assertRaises(ValueError):
            self.tool("cash_drawer_record_entry")("nope", 1)

    def test_undo_takes_exactly_one_target(self):
        f = self.tool("cash_drawer_undo")
        for kw in ({}, {"entry": "E", "close": "C"}):
            with self.assertRaises(ValueError):
                f(**kw, confirm=True)
        f(close="CDC-1", confirm=True)
        self.assertEqual(self.client.calls, [("call_method", CD + "undo_close", {"close": "CDC-1"})])

    def test_record_payout_kwargs(self):
        self.tool("cash_drawer_record_payout")("ACQ-1", "Check", confirm=True)
        self.assertEqual(self.client.calls[0],
                         ("call_method", CD + "record_payout", {"acquisition": "ACQ-1", "method": "Check"}))

    def test_expense_receipt_rule_is_documented_for_the_remote_connector(self):
        doc = self.tool("cash_drawer_record_entry").__doc__
        self.assertIn("SAME POS user", doc)
        self.assertIn("remote connector has no upload tool", doc)


class Storage(ShopfloorBase):
    def test_map_and_zones(self):
        self.tool("storage_map")("OSA")
        self.tool("storage_map")(zones_only=True)
        self.assertEqual([c[1] for c in self.client.calls], [ST + "get_map", ST + "get_options"])

    def test_location_and_unassigned(self):
        self.tool("storage_location")("A1")
        self.tool("storage_unassigned")()
        self.assertEqual(self.client.calls, [
            ("call_method", ST + "get_location", {"location": "A1"}),
            ("call_method", ST + "get_unassigned", {"company": None})])

    def test_where_serial_and_items(self):
        out = self.tool("storage_where")(serial_no=" SN1 ", item_codes=["I1", "I2"])
        kind, doctype, kw = self.client.calls[0]
        self.assertEqual((kind, doctype), ("list_documents", "Serial No"))
        self.assertEqual(kw["filters"], [["name", "=", "SN1"]])
        self.assertIn("storage_location", kw["fields"])
        self.assertEqual(self.client.calls[1],
                         ("call_method", ST + "stock_locations",
                          {"item_codes": ["I1", "I2"], "company": None}))
        self.assertEqual(set(out), {"serial", "items"})

    def test_where_needs_something(self):
        with self.assertRaises(ValueError):
            self.tool("storage_where")()

    def test_where_unknown_serial_is_none_not_an_error(self):
        self.client.list_documents = lambda *a, **k: []
        self.assertEqual(self.tool("storage_where")(serial_no="NOPE"), {"serial": None})

    def test_zone_and_position_writes(self):
        self.tool("storage_create_zone")("Rack 1", "Open", confirm=True)
        self.tool("storage_create_zone")("A", "Slots", 30, sides=2, numbering="In order", confirm=True)
        self.tool("storage_add_positions")("A", 3, confirm=True)
        self.tool("storage_set_disabled")(False, location="A2", confirm=True)
        self.assertEqual([c[2] for c in self.client.calls], [
            {"zone_name": "Rack 1", "kind": "Open", "count": 1, "company": None},  # no sides: older POS
            {"zone_name": "A", "kind": "Slots", "count": 30, "company": None, "sides": 2,
             "numbering": "In order"},
            {"zone": "A", "count": 3},
            {"disabled": 0, "zone": None, "location": "A2"},
        ])

    def test_set_disabled_takes_exactly_one_target(self):
        for kw in ({}, {"zone": "A", "location": "A1"}):
            with self.assertRaises(ValueError):
                self.tool("storage_set_disabled")(True, **kw, confirm=True)
        self.assertEqual(self.client.calls, [])

    def test_move_undo_confirm_taken(self):
        self.tool("storage_scan_move")("A1", "0123", qty=4, from_location="B2", confirm=True)
        self.tool("storage_undo_move")("SM-1", confirm=True)
        self.tool("storage_confirm_taken")("I", "A1", 2, confirm=True)
        self.assertEqual([c[2] for c in self.client.calls], [
            {"to_location": "A1", "code": "0123", "qty": 4, "from_location": "B2"},
            {"move": "SM-1"},
            {"item_code": "I", "location": "A1", "qty": 2, "company": None},
        ])


class CpaSurface(unittest.TestCase):
    READS = {"cash_drawer_closes", "cash_drawer_entries", "cash_drawer_log", "cash_drawer_weekly",
             "inventory_counts", "inventory_count_variance"}

    def test_cpa_gets_exactly_these_shopfloor_tools_and_no_writes(self):
        mcp = FakeMCP()
        shopfloor.register(modes.FilteredMCP(mcp, modes.CPA_TOOL_NAMES))
        self.assertEqual(set(mcp.tools), self.READS)

    def test_no_shopfloor_write_is_in_the_cpa_allowlist(self):
        self.assertFalse(set(WRITES) & set(modes.CPA_TOOL_NAMES))

    def test_cpa_method_allowlist_has_the_two_count_reads_and_no_writes(self):
        self.assertTrue({IC + "get_counts", IC + "variance"} <= modes.CPA_METHOD_ALLOWLIST)
        for _name, (_kw, method) in WRITES.items():
            self.assertNotIn(method, modes.CPA_METHOD_ALLOWLIST)
        # not the other reads either: only what the owner asked for
        for method in (IC + "get_state", CD + "get_today", ST + "get_map"):
            self.assertNotIn(method, modes.CPA_METHOD_ALLOWLIST)

    def _cpa_client(self):
        from gunstore_mcp import frappe_client as fc
        from gunstore_mcp.config import Config
        cfg = Config(base_url="http://test.invalid", api_key="k", api_secret="s",
                     timeout=5, write_denylist=frozenset())
        with patch.object(fc, "get_config", return_value=cfg), \
                patch.dict("os.environ", {"GUNSTORE_MCP_MODE": "cpa"}):
            client = FrappeClient()
        calls = []
        client._request = lambda *a, **kw: calls.append(a) or []
        return client, calls

    def test_cpa_client_lets_the_reads_through_and_refuses_every_write_method(self):
        client, calls = self._cpa_client()
        client.call_method(IC + "get_counts", {})
        client.call_method(IC + "variance", {"count": "C1"})
        client.list_documents("Cash Drawer Close")
        client.list_documents("Cash Drawer Entry")
        client.run_report("Cash Drawer Weekly", {"company": "X"})
        self.assertEqual(len(calls), 5)
        for _name, (_kw, method) in WRITES.items():
            with self.subTest(method=method):
                with self.assertRaises(modes.CpaModeRefused):
                    client.call_method(method, {})
        self.assertEqual(len(calls), 5)


if __name__ == "__main__":
    unittest.main()

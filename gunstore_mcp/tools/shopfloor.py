"""Shop-floor tools: stocktake (Inventory Count), the Cash Drawer, Storage Locations.

POS 1.5.0-beta.15 shipped three counter-side features, each with its own page
backend (`ffl_core/api/inventory_count.py`, `cash_drawer.py`, `storage.py`) plus the
`Cash Drawer Weekly` script report. Every signature below was checked against
gunstore-pos `origin/develop` before being wrapped; the tests are mocked, so drift
would only show against a live POS.

Split by consequence:

* **reads** — registered everywhere. Six of them (`inventory_counts`,
  `inventory_count_variance`, `cash_drawer_closes`, `cash_drawer_entries`,
  `cash_drawer_log`, `cash_drawer_weekly`) are also on the read-only cpa surface;
  see modes.py.
* **writes** — every one needs ``confirm=true`` (owner decision: all of it is exposed,
  including the money and stock actions). They are NOT behind a registration-time env
  gate like the GunBroker / distributor actions, on purpose: each is the ordinary
  counter or back-office action the POS page offers to the same roles, the POS
  enforces those roles per call, and the remote connector records every call in the
  Connector Audit Log. The dotted methods are also in generic._ALWAYS_CONFIRM_METHODS,
  so ``frappe_run_method`` cannot reach them without confirm either.

What these tools never do: touch a firearm's A&D record. A stocktake REPORTS missing
guns and never adjusts them (no disposition, no FastBound call); storage moves never
create a stock or accounting document.
"""
from __future__ import annotations

from typing import Any

from ..frappe_client import get_client
from ..safety import require_confirm
from .reports import _default_company

_IC = "ffl_core.api.inventory_count."
_CD = "ffl_core.api.cash_drawer."
_ST = "ffl_core.api.storage."

_CLOSE_FIELDS = [
    "name", "company", "business_date", "status", "is_counted", "is_cutoff", "expected",
    "counted", "variance", "opening_amount", "carry_forward", "reason", "counted_by",
    "counted_at", "closed_at", "days_covered", "covers_from", "variance_je", "cutoff_je",
]
_ENTRY_FIELDS = [
    "name", "company", "entry_type", "status", "posting_date", "amount", "payment_method",
    "reference", "withdrawer", "expense_account", "memo", "receipt", "acquisition", "serial",
    "debit_account", "credit_account", "journal_entry", "voided_by", "voided_on", "creation",
]
_MAX_ROWS = 500  # lazy: one page per call; narrow the dates (or raise this) for more.

# Which extra arguments each cash-entry kind takes. Anything else passed with that
# kind is refused: silently dropping a receipt on a deposit would hide a mistake.
_ENTRY_ARGS = {
    "deposit": ("reference",),
    "from_bank": ("reference",),
    "expense": ("expense_account", "memo", "receipt"),
}
_ENTRY_REQUIRED = {
    "deposit": (),
    "from_bank": (),
    "expense": ("expense_account", "memo", "receipt"),
}
_ENTRY_METHOD = {
    "deposit": "record_deposit",
    "from_bank": "record_from_bank",
    "expense": "record_expense",
}


def _rows(limit: int) -> int:
    return max(1, min(int(limit), _MAX_ROWS))


def register(mcp: Any) -> None:
    # ===================================================== inventory count: reads

    @mcp.tool()
    def inventory_counts() -> Any:
        """The 50 most recent stocktakes (Inventory Counts), newest first: name, status
        (Open | Finalized | Cancelled), company, who/when, the Stock Reconciliation it
        posted, how many scans it holds and its warehouse / item-group scope. Read-only.
        Needs a POS user with a stock role."""
        return get_client().call_method(_IC + "get_counts")

    @mcp.tool()
    def inventory_count_state(count: str, counts_only: bool = False) -> Any:
        """Everything the Inventory Count page shows for one count: the guns scanned so
        far (who, when), counted quantities, unknown barcodes and — unless counts_only —
        the expected guns and the per-item expected-vs-counted rows. counts_only=true is
        the light version (scans only). Read-only."""
        return get_client().call_method(
            _IC + "get_state", {"count": count, "counts_only": 1 if counts_only else 0})

    @mcp.tool()
    def inventory_count_variance(count: str) -> Any:
        """Counted vs. what the system says, for one count. serial.{missing, unexpected,
        unknown} lists FIREARM differences — reported only, never adjusted by the POS
        (a missing gun needs a person to look at it, possibly an ATF theft/loss
        report). items[] are the non-serialized differences that finalize would adjust:
        expected, counted, moved (sold/arrived since counted), adjust_to, difference,
        never_scanned, and open_register_sales (a row with any cannot be adjusted until the
        register closes). skipped[] cannot be reconciled and must be fixed by hand.
        A finalized count returns the report saved at finalize. Read-only — read this
        before inventory_count_finalize."""
        return get_client().call_method(_IC + "variance", {"count": count})

    # ===================================================== inventory count: writes

    @mcp.tool()
    def inventory_count_create(
        company: str | None = None, warehouses: list[str] | None = None,
        item_groups: list[str] | None = None, include_custody: bool = True,
        notes: str | None = None, confirm: bool = False,
    ) -> Any:
        """Start a stocktake. No warehouses and no item groups = the whole store. A
        warehouse must be one of this company's own stock warehouses (not Transit,
        consignment or disabled — the refusal lists the bad ones); item_groups include
        their children. include_custody=false leaves customer-custody warehouses out.
        Returns {name, company, warehouses, item_groups}; use name as `count` below.
        Creates a document — confirm=true."""
        require_confirm("inventory_count_create", confirm)
        return get_client().call_method(_IC + "create_count", {
            "company": company, "warehouses": warehouses, "item_groups": item_groups,
            "include_custody": 1 if include_custody else 0, "notes": notes})

    @mcp.tool()
    def inventory_count_scan(
        count: str, code: str, warehouse: str | None = None, confirm: bool = False
    ) -> Any:
        """Record one scan into an OPEN count: a firearm's serial number (adds that gun
        once; scanning it again changes nothing) or an item's barcode (UPC; adds 1 of that
        item in `warehouse`, optional when the count covers one warehouse or includes the
        default one). A code that is neither is saved as an unknown barcode.
        Returns {result: serial | already | item | unknown | error, message, entry, ...}.
        result=error means NOTHING was saved (it is a reply, not an exception). `entry`
        is the row to hand to inventory_count_undo. confirm=true."""
        require_confirm(f"inventory_count_scan {count} {code}", confirm)
        return get_client().call_method(
            _IC + "scan", {"count": count, "code": code, "warehouse": warehouse})

    @mcp.tool()
    def inventory_count_set_qty(
        count: str, item_code: str, warehouse: str, qty: float, confirm: bool = False
    ) -> Any:
        """Type in the counted quantity (0 or more) of a NON-serialized item that has no
        barcode, in an open count. Saved as the difference from what is already counted,
        so another device's scans are not overwritten; a first count of 0 is recorded too
        ("none on the shelf"). Returns {ok, counted, entry, ...}; entry is null when
        nothing changed. confirm=true."""
        require_confirm(f"inventory_count_set_qty {count} {item_code} {qty}", confirm)
        return get_client().call_method(_IC + "set_qty", {
            "count": count, "item_code": item_code, "warehouse": warehouse, "qty": qty})

    @mcp.tool()
    def inventory_count_toggle_serial(
        count: str, serial: str, found: bool, confirm: bool = False
    ) -> Any:
        """Tick (found=true) or untick (found=false) a firearm by hand in an open count,
        for a gun whose label will not scan. Unticking someone else's scan needs a Stock
        Manager. Returns {ok, found, entry}. confirm=true."""
        require_confirm(f"inventory_count_toggle_serial {count} {serial}", confirm)
        return get_client().call_method(
            _IC + "toggle_serial", {"count": count, "serial": serial, "found": 1 if found else 0})

    @mcp.tool()
    def inventory_count_undo(entry: str, confirm: bool = False) -> Any:
        """Remove one scan / typed entry (an Inventory Count Entry name, from the scan or
        set_qty reply) from an OPEN count. Only its author or a Stock Manager may; an entry
        that is already gone counts as done. confirm=true."""
        require_confirm(f"inventory_count_undo {entry}", confirm)
        return get_client().call_method(_IC + "undo", {"entry": entry})

    @mcp.tool()
    def inventory_count_cancel(count: str, confirm: bool = False) -> Any:
        """Cancel an OPEN count without adjusting anything (wrong start / abandoned walk).
        The scans are kept for the record. Needs a Stock Manager. confirm=true."""
        require_confirm(f"inventory_count_cancel {count}", confirm)
        return get_client().call_method(_IC + "cancel_count", {"count": count})

    @mcp.tool()
    def inventory_count_finalize(
        count: str, item_rows: list[dict] | None = None, confirm: bool = False
    ) -> Any:
        """⚠ Finalize a count: posts ONE STOCK RECONCILIATION that sets the non-serialized
        items to the counted quantities (changes on-hand stock and the stock-adjustment
        books), and saves the firearm report. Firearms are NEVER adjusted: no disposition,
        no FastBound call — missing guns are only listed.

        item_rows = [{item_code, warehouse}, …] picks which differences to adjust. Omitted
        = every row with a difference EXCEPT items nobody scanned (those are written to 0
        only when listed here). Quantities are recomputed server-side. Refused, with
        nothing posted, while a register sale is open on a row being adjusted, in a frozen
        period, or when nothing was scanned. Cancelling the posted reconciliation later is
        a Desk job. Read inventory_count_variance first and confirm the rows with the
        user. Needs a Stock Manager. Requires confirm=true."""
        require_confirm(f"inventory_count_finalize {count} (posts a Stock Reconciliation)", confirm)
        return get_client().call_method(
            _IC + "finalize", {"count": count, "item_rows": item_rows})

    # ===================================================== cash drawer: reads

    @mcp.tool()
    def cash_drawer_today(company: str | None = None) -> Any:
        """Everything the Cash Drawer page shows: what the drawer should hold now
        (`expected` = the Cash account's balance plus unmerged POS cash), today's / since-
        the-last-close lines (kind, amount, entry name, can_undo), the last close and
        whether today is counted, open POS shifts, the variance threshold, the expense cap,
        allowed expense accounts, and seller payments made by bank.
        Read-only; creates nothing. Needs a counter role."""
        return get_client().call_method(_CD + "get_today", {"company": company})

    @mcp.tool()
    def cash_drawer_closes(
        company: str | None = None, from_date: str | None = None,
        to_date: str | None = None, include_undone: bool = False, limit: int = 30,
    ) -> Any:
        """Cash Drawer Close rows (the daily count), newest first: business_date, expected,
        counted, variance, reason, the variance / first-count journal entry, who counted
        and how many days the count covers. Undone closes are left out unless
        include_undone=true. from_date / to_date filter business_date (YYYY-MM-DD).
        Read-only; also on the cpa surface."""
        client = get_client()
        filters: list = [["company", "=", company or _default_company(client)]]
        if not include_undone:
            filters.append(["status", "!=", "Undone"])
        if from_date:
            filters.append(["business_date", ">=", from_date])
        if to_date:
            filters.append(["business_date", "<=", to_date])
        return client.list_documents(
            "Cash Drawer Close", fields=_CLOSE_FIELDS, filters=filters, limit=_rows(limit),
            order_by="business_date desc, creation desc")

    @mcp.tool()
    def cash_drawer_entries(
        company: str | None = None, from_date: str | None = None,
        to_date: str | None = None, entry_type: str | None = None,
        status: str | None = "Posted", limit: int = 100,
    ) -> Any:
        """Cash Drawer Entry rows — the cash that left or entered outside a sale — newest
        first, with the journal entry each booked. entry_type: Deposit | From Bank |
        Expense | Payout (seller paid for a gun) | Payout Adjustment | Owner Draw (old entries
        only; no longer made) (empty = all).
        status: Posted (default) | Voided (empty = both). from_date / to_date filter
        posting_date. Expenses carry the receipt file URL. Read-only; also on the cpa
        surface."""
        client = get_client()
        filters: list = [["company", "=", company or _default_company(client)]]
        if entry_type:
            filters.append(["entry_type", "=", entry_type])
        if status:
            filters.append(["status", "=", status])
        if from_date:
            filters.append(["posting_date", ">=", from_date])
        if to_date:
            filters.append(["posting_date", "<=", to_date])
        return client.list_documents(
            "Cash Drawer Entry", fields=_ENTRY_FIELDS, filters=filters, limit=_rows(limit),
            order_by="posting_date desc, creation desc")

    @mcp.tool()
    def cash_drawer_weekly(
        from_date: str, to_date: str, company: str | None = None
    ) -> Any:
        """The Cash Drawer Weekly report, UNCHANGED (columns + rows), for the bookkeeper:
        one row per day with opening books, register sales, counter orders, customer
        payments, cash refunds, seller payouts, deposits, cash from the bank, cash expenses, count
        difference, closing books, expected / counted / variance and the reason — plus a
        proof block (daily lines vs the Cash ledger, must be 0.00; books vs the last count;
        any UNCLASSIFIED ledger line, which should be empty). Range ≤ 400 days (YYYY-MM-DD).
        company defaults to the POS default company. Read-only; also on the cpa surface."""
        client = get_client()
        return client.run_report("Cash Drawer Weekly", {
            "company": company or _default_company(client),
            "from_date": from_date, "to_date": to_date})

    @mcp.tool()
    def cash_drawer_log(
        from_date: str | None = None, to_date: str | None = None, company: str | None = None
    ) -> Any:
        """The Cash Drawer page's Log: every cash movement in a date range, oldest first —
        register sales (one line per POS invoice, even after the nightly merge), counter
        orders, customer payments, refunds, seller payouts, deposits, cash from the bank,
        expenses, count differences and the first count — each with who posted it, the
        document, the amount (+ in / − out) and the drawer balance after it. Also the
        opening balance (books before from_date), closing, and cash in / out totals (count
        differences are in neither). Dates YYYY-MM-DD; default the last 7 days; at most a
        year. Read-only; also on the cpa surface."""
        client = get_client()
        return client.call_method(_CD + "get_log", {
            "company": company or _default_company(client), "from_date": from_date,
            "to_date": to_date})

    @mcp.tool()
    def cash_drawer_preview_close(counted: float, company: str | None = None) -> Any:
        """What closing the day with this counted amount WOULD do, recording nothing:
        {expected, counted, variance (counted − expected; negative = short), threshold,
        first_count, needs_reason}. Do this before cash_drawer_close_day and pass its
        `expected` as expected_seen. Read-only. Needs a counter role."""
        return get_client().call_method(
            _CD + "preview_close", {"company": company, "counted": counted})

    # ===================================================== cash drawer: writes

    @mcp.tool()
    def cash_drawer_close_day(
        counted: float, company: str | None = None, reason: str | None = None,
        expected_seen: float | None = None, confirm: bool = False,
    ) -> Any:
        """⚠ Count the drawer and CLOSE THE DAY. Closes the open POS shifts (they stay
        closed even if the count is undone later), books any difference as a journal entry
        (Cash Over/Short; the company's very FIRST count instead sets the books to the
        physical drawer against the bank — a CASH-CUTOFF entry), and carries the count
        forward as tomorrow's opening. A difference at or above the Cash Drawer Settings
        limit needs a `reason`; a smaller one never blocks. Every uncounted day since the
        last count is absorbed (the reply says how many).

        Run cash_drawer_preview_close first and pass its `expected` as expected_seen — the
        close is refused if the books moved since. Reversible only by cash_drawer_undo (the
        latest close, a manager). Requires confirm=true."""
        require_confirm(f"cash_drawer_close_day counted={counted} (books a variance / cutoff entry)",
                        confirm)
        return get_client().call_method(_CD + "close_day", {
            "company": company, "counted": counted, "reason": reason,
            "expected_seen": expected_seen})

    @mcp.tool()
    def cash_drawer_record_entry(
        kind: str, amount: float, company: str | None = None,
        reference: str | None = None,
        memo: str | None = None, expense_account: str | None = None,
        receipt: str | None = None, confirm: bool = False,
    ) -> Any:
        """⚠ Record cash leaving or entering the drawer outside a sale — books a journal
        entry. kind:

        * deposit — cash taken to the bank (Dr Operating Bank / Cr Cash). Optional:
          reference (slip number).
        * from_bank — cash taken out of the bank and put in the drawer (Dr Cash / Cr
          Operating Bank). Any counter role. Optional: reference (withdrawal slip).
        * expense — a small cash expense (Dr the expense account / Cr Cash), capped (200 by
          default) and only to an allowed account (cash_drawer_today lists them). Needs
          expense_account, memo and receipt.

        RECEIPT RULE (expense): receipt is the file_url of a photo that the SAME POS user
        uploaded within the last day, still private and attached to nothing, and that no
        other expense has used — the POS refuses the call otherwise, before booking
        anything. The remote connector has no upload tool, so the signed-in user uploads
        the photo themselves in the POS (a private File, not attached to a document) and
        you pass its file_url; the local (stdio) server can use upload_attachment with
        is_private=true and no doctype/name. Arguments that do not belong to the kind are
        refused. Undo with cash_drawer_undo until the next count. Requires confirm=true."""
        kind = (kind or "").strip().lower()
        if kind not in _ENTRY_ARGS:
            raise ValueError(f"kind must be one of {', '.join(_ENTRY_ARGS)} (got {kind!r}).")
        given = {"reference": reference, "memo": memo,
                 "expense_account": expense_account, "receipt": receipt}
        stray = sorted(k for k, v in given.items() if v and k not in _ENTRY_ARGS[kind])
        if stray:
            raise ValueError(f"{stray} do not apply to a {kind} entry — refusing rather than "
                             "dropping them silently.")
        missing = [k for k in _ENTRY_REQUIRED[kind] if not (given[k] or "").strip()]
        if missing:
            raise ValueError(f"a {kind} entry needs {', '.join(_ENTRY_REQUIRED[kind])} "
                             f"(missing: {', '.join(missing)}).")
        require_confirm(f"cash_drawer_record_entry {kind} ${amount}", confirm)
        kwargs: dict[str, Any] = {"company": company, "amount": amount}
        kwargs.update({k: given[k] for k in _ENTRY_ARGS[kind]})
        return get_client().call_method(_CD + _ENTRY_METHOD[kind], kwargs)

    @mcp.tool()
    def cash_drawer_undo(
        entry: str | None = None, close: str | None = None, confirm: bool = False
    ) -> Any:
        """⚠ Undo cash-drawer bookkeeping — give exactly ONE of:

        * entry — a Cash Drawer Entry (deposit, cash from the bank, expense or seller payment):
          its journal entry is cancelled. Refused once a count was taken after it; undoing
          a seller payment (and its cost corrections) needs a System Manager.
        * close — the LATEST Cash Drawer Close of its company: its difference / first-count
          entry is cancelled and the day reads as not counted again (POS shifts it closed
          stay closed).

        Manager roles only. Requires confirm=true."""
        if bool(entry) == bool(close):
            raise ValueError("give exactly one of entry or close.")
        require_confirm(f"cash_drawer_undo {entry or close} (cancels a journal entry)", confirm)
        if entry:
            return get_client().call_method(_CD + "undo_entry", {"entry": entry})
        return get_client().call_method(_CD + "undo_close", {"close": close})

    @mcp.tool()
    def cash_drawer_record_payout(acquisition: str, method: str, confirm: bool = False) -> Any:
        """⚠ Record how the PRIVATE SELLER of an acquired firearm was paid (trade-in or other
        purchase from an individual): method Cash | Zelle | Check | ACH. Books Dr Stock
        Adjustment / Cr Cash (or the bank) at exactly the acquisition's cost, once per
        acquisition. If a payout is ALREADY recorded this REPLACES it (the old one is undone
        and the same net booked with the new method) — a System Manager's fix for a wrong
        method. Refused for a purchase that was spent as a credit on a sale. Manager roles
        only. Requires confirm=true."""
        require_confirm(f"cash_drawer_record_payout {acquisition} via {method}", confirm)
        return get_client().call_method(
            _CD + "record_payout", {"acquisition": acquisition, "method": method})

    # ===================================================== storage: reads

    @mcp.tool()
    def storage_map(company: str | None = None, zones_only: bool = False) -> Any:
        """The store map: every zone (Slots = numbered one-gun positions; Open = one
        location holding anything) with each position's contents — guns (serial, item) and
        non-serialized qty — and `unassigned` counts (guns / items in no location, items
        sold without saying where from = `to_confirm`). zones_only=true returns just
        {company, zones: [{name, zone_name, kind, disabled, positions}], can_manage} — the
        cheap way to list zones. Read-only; touches no stock or books."""
        method = "get_options" if zones_only else "get_map"
        return get_client().call_method(_ST + method, {"company": company})

    @mcp.tool()
    def storage_location(location: str) -> Any:
        """What is in ONE location (its name is its barcode): the location row, its Active
        guns (serial, item, make/model/caliber) and its non-serialized items with qty.
        Read-only."""
        return get_client().call_method(_ST + "get_location", {"location": location})

    @mcp.tool()
    def storage_where(
        serial_no: str | None = None, item_codes: list[str] | None = None,
        company: str | None = None,
    ) -> Any:
        """Where is it? serial_no → that Serial No's {name, item_code, status, warehouse,
        storage_location} (storage_location empty = in no location yet; open it with
        storage_location). item_codes → per non-serialized item {locations: [{location,
        label, zone, qty}], unassigned, total, sole} — `unassigned` is stock in no location,
        `sole` the single location it can only have come from (else null). Give either or
        both. Read-only."""
        if not serial_no and not item_codes:
            raise ValueError("give serial_no and/or item_codes.")
        client = get_client()
        out: dict[str, Any] = {}
        if serial_no:
            rows = client.list_documents(
                "Serial No",
                fields=["name", "item_code", "status", "warehouse", "storage_location"],
                filters=[["name", "=", serial_no.strip()]], limit=1)
            out["serial"] = rows[0] if rows else None
        if item_codes:
            out["items"] = client.call_method(
                _ST + "stock_locations", {"item_codes": item_codes, "company": company})
        return out

    @mcp.tool()
    def storage_unassigned(company: str | None = None) -> Any:
        """What is not in a location yet, and what the locations over-claim: guns[] with no
        location, items[] {item_code, stock, located, unassigned}, and to_confirm[] — items
        sold or shipped without saying which location they came from {stock, located, over};
        resolve those with storage_confirm_taken. Read-only."""
        return get_client().call_method(_ST + "get_unassigned", {"company": company})

    # ===================================================== storage: writes
    # Tracking layer only: none of these creates a stock or accounting document.

    @mcp.tool()
    def storage_create_zone(
        zone_name: str, kind: str, count: int = 1, company: str | None = None,
        confirm: bool = False,
    ) -> Any:
        """Create a storage zone and its positions. kind: Slots (positions named A1, A2, …,
        ONE firearm each — `count` says how many) or Open (ONE location named like the
        zone, no limit, holds firearms and everything else; `count` is ignored — create one
        zone per rack, case or safe). Stock Manager. Returns {zone, positions}. confirm=true."""
        require_confirm(f"storage_create_zone {zone_name} ({kind})", confirm)
        return get_client().call_method(_ST + "create_zone", {
            "zone_name": zone_name, "kind": kind, "count": count, "company": company})

    @mcp.tool()
    def storage_add_positions(zone: str, count: int, confirm: bool = False) -> Any:
        """Add `count` slots to a Slots zone (existing ones are never renumbered or removed;
        refused for an Open zone). Stock Manager. confirm=true."""
        require_confirm(f"storage_add_positions {zone} +{count}", confirm)
        return get_client().call_method(_ST + "add_positions", {"zone": zone, "count": count})

    @mcp.tool()
    def storage_set_disabled(
        disabled: bool, zone: str | None = None, location: str | None = None,
        confirm: bool = False,
    ) -> Any:
        """Disable (disabled=true) or re-enable (false) a whole zone with its positions, or
        ONE position — give exactly one of zone / location. A disabled place takes nothing
        new, and disabling needs it to be empty. Stock Manager. confirm=true."""
        if bool(zone) == bool(location):
            raise ValueError("give exactly one of zone or location.")
        require_confirm(
            f"storage_set_disabled {zone or location} -> {'disabled' if disabled else 'enabled'}",
            confirm)
        return get_client().call_method(_ST + "set_disabled", {
            "disabled": 1 if disabled else 0, "zone": zone, "location": location})

    @mcp.tool()
    def storage_scan_move(
        to_location: str, code: str, qty: float = 1, from_location: str | None = None,
        confirm: bool = False,
    ) -> Any:
        """Put what was scanned INTO a location — this one call is both "assign a firearm
        to a location" (code = serial number) and "put non-serialized units there" (code =
        UPC or item code, qty units). Units come from from_location if given, else from the
        units in no location, else from the one other location holding them; if they could
        come from several, NOTHING moves and result=choose lists `options` — call again with
        from_location. A location's own name as `code` is refused. Returns {result: serial |
        item | already | choose | error, message, move, ...}; error is a reply (nothing
        moved), and `move` is what storage_undo_move takes. Tracking only — no stock or
        books change. confirm=true."""
        require_confirm(f"storage_scan_move {code} -> {to_location}", confirm)
        return get_client().call_method(_ST + "scan_move", {
            "to_location": to_location, "code": code, "qty": qty,
            "from_location": from_location})

    @mcp.tool()
    def storage_undo_move(move: str, confirm: bool = False) -> Any:
        """Take back a move made by storage_scan_move or storage_confirm_taken (a Storage Move
        name): the gun / units go back where they were. Only a hand move, only once, and only
        while the things are still where that move left them. Returns {ok, message}
        — ok=false is a reply explaining why it can no longer be undone. confirm=true."""
        require_confirm(f"storage_undo_move {move}", confirm)
        return get_client().call_method(_ST + "undo_move", {"move": move})

    @mcp.tool()
    def storage_confirm_taken(
        item_code: str, location: str, qty: float, company: str | None = None,
        confirm: bool = False,
    ) -> Any:
        """Resolve a "to confirm" item (see storage_unassigned): say which `location` `qty`
        sold-or-shipped units really came from. They leave that location; stock and the
        books are untouched. qty must not exceed what is waiting (`over`). This is the only
        "take quantity out of a location" action. Returns {ok, move, over}. confirm=true."""
        require_confirm(f"storage_confirm_taken {item_code} x{qty} from {location}", confirm)
        return get_client().call_method(_ST + "confirm_taken", {
            "item_code": item_code, "location": location, "qty": qty, "company": company})

"""Loads the SKU lookup (supplier SKU -> our SKU) fresh from the live
StockFeeds.xlsm on the management server every run -- port of ImportSKU
(Module1.bas:651).

Deliberately does NOT read from any local/cached copy of either workbook:
the whole point is to avoid alerting against a stale SKU map. Every call
re-fetches from STOCKFEEDS_XLSM_PATH, decrypts into a temp file, reads it,
and deletes the temp file -- read-only against the server, no write-back.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import msoffcrypto
import openpyxl


def load_sku_lookup(server_path: str | Path, password: str, sheet_name: str = "Inventory") -> dict[str, str]:
    """Return {supplier_sku: our_sku} read fresh from the server's StockFeeds.xlsm.

    Column A = supplier SKU, column D = OURSKU -- same mapping ImportSKU
    copies into the old workbook's "SKU Lookup" sheet (confirmed live
    against the real file: header row is SKU, V LOOK UP SKU,
    UNIT QUANTITY - CA, OURSKU, SUPPLIER).
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsm")
    os.close(fd)
    try:
        with open(server_path, "rb") as locked:
            office_file = msoffcrypto.OfficeFile(locked)
            office_file.load_key(password=password)
            with open(tmp_path, "wb") as decrypted:
                office_file.decrypt(decrypted)

        wb = openpyxl.load_workbook(tmp_path, data_only=True, read_only=True)
        try:
            ws = wb[sheet_name]
            lookup: dict[str, str] = {}
            rows = ws.iter_rows(min_row=2, max_col=4, values_only=True)
            for row in rows:
                supplier_sku, _v_lookup_sku, _qty, our_sku = row
                if supplier_sku is None or our_sku is None:
                    continue
                lookup[str(supplier_sku)] = str(our_sku)
            return lookup
        finally:
            wb.close()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

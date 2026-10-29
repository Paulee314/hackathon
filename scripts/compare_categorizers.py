"""Compare two categorizer runs merchant by merchant: same treatment? same Schedule C line? same business line
(rideshare, Turo or both, which only moves money between lines of the P&L)? Dollars affected.

    python3 scripts/compare_categorizers.py tests/fixtures/categories.json data/ledger/categories_open.json
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

a_path, b_path = map(Path, sys.argv[1:3])
a, b = json.loads(a_path.read_text()), json.loads(b_path.read_text())
keys = sorted(set(a) | set(b))
same_t = same_both = 0
diffs, dollars = [], Decimal("0")
line_diffs = []
for k in keys:
    x, y = a.get(k), b.get(k)
    if not x or not y:
        diffs.append((k, x and (x["treatment"], x["schedule_c_line"]), y and (y["treatment"], y["schedule_c_line"]),
                      (x or y)["total"]))
        continue
    t_ok = x["treatment"] == y["treatment"]
    l_ok = x["schedule_c_line"] == y["schedule_c_line"]
    same_t += t_ok
    same_both += t_ok and l_ok
    if x.get("business_line") != y.get("business_line"):
        line_diffs.append((k, x.get("treatment"), x.get("business_line"), y.get("business_line"), x["total"]))
    if not (t_ok and l_ok):
        diffs.append((k, (x["treatment"], x["schedule_c_line"]), (y["treatment"], y["schedule_c_line"]), x["total"]))
        dollars += Decimal(x["total"])
total = sum(Decimal((a.get(k) or b.get(k))["total"]) for k in keys)
print(f"{len(keys)} merchants, ${total:,.2f} of spending")
print(f"same treatment: {same_t} of {len(keys)}; same treatment and Schedule C line: {same_both} of {len(keys)}")
print(f"dollars where they differ: ${dollars:,.2f}")
for k, x, y, amt in diffs:
    print(f"  {k:<28} ${Decimal(amt):>9,.2f}   {a_path.stem}: {x}   {b_path.stem}: {y}")
print(f"business line differs on {len(line_diffs)}:")
for k, t, x, y, amt in line_diffs:
    print(f"  {k:<28} ${Decimal(amt):>9,.2f}   {t}: {x} -> {y}" + ("   (personal, so no effect)" if t == "personal" else ""))

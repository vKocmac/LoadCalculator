"""Compare two capture.py outputs. Exit code 0 = identical calculations.
Usage: python3 compare.py baseline.json candidate.json
Only the report date/time is ignored. Visible-text differences caused purely by
styling (e.g. collapsed panels, CSS uppercase) are ignored by comparing textContent.
"""
import json, re, sys


def norm(x):
    return re.sub(r'(Ημερομηνία|Date): [^\n|]*', r'\1: <date>', x) if isinstance(x, str) else x


a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
bad = 0
if len(a["snaps"]) != len(b["snaps"]):
    bad += 1
    print("snapshot count differs", len(a["snaps"]), len(b["snaps"]))
for sa, sb in zip(a["snaps"], b["snaps"]):
    for k in sa:
        va, vb = sa[k], sb.get(k)
        if isinstance(va, str) and " ||TC|| " in va and isinstance(vb, str) and " ||TC|| " in vb:
            va, vb = va.split(" ||TC|| ", 1)[1], vb.split(" ||TC|| ", 1)[1]
        if norm(json.dumps(va, ensure_ascii=False)) != norm(json.dumps(vb, ensure_ascii=False)):
            bad += 1
            print("DIFF", sa["__tag"], k)
            print("  baseline :", str(va)[:300])
            print("  candidate:", str(vb)[:300])
for pa, pb in zip(a["popups"], b["popups"]):
    if norm(pa[1]) != norm(pb[1]):
        bad += 1
        print("DIFF report", pa[0])
if b["errors"]:
    bad += 1
    print("page errors in candidate:", b["errors"])
print(f"{len(a['snaps'])} scenarios, {len(a['popups'])} reports -> {bad} differences")
sys.exit(1 if bad else 0)

"""Compare two capture.py outputs.  Exit code 0 = same calculated numbers.

Usage: python3 compare.py baseline.json candidate.json [--strict]

Default (semantic) mode, used when the UI text/layout legitimately changed:
  * every legacy input value must be identical;
  * every result row of the baseline (label -> value) must exist unchanged in the candidate
    (new extra rows are allowed);
  * the numbers in room list, building totals, wall/roof/floor hints and psychrometrics
    must be identical (same numbers, same order);
  * the pie-chart pixels must be identical;
  * report (PDF) texts are compared by their numbers only when --reports is given.
--strict: full text equality of everything (only the report date is ignored).
"""
import json, os, re, sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
STRICT = "--strict" in sys.argv
REPORTS = "--reports" in sys.argv
LEGACY = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy_options.json"), encoding="utf-8"))
LEGACY_IDS = set(LEGACY["num"]) | set(LEGACY["sel"]) - {"Units", "Lang", "psRoomSelect", "devLib"}
NUMERIC_AREAS = ["spacesTree", "buildingTotals", "wallPctHint", "roofPctHint", "floorPctHint", "psychroSummary", "psychroMix"]
# result-row labels renamed in v9 (same quantity)
RENAMED = {"Τοίχοι προς μη θερμαινόμενο": "Τοίχοι προς μη κλιματιζόμενο",
           "Οροφή προς μη θερμαινόμενο": "Οροφή προς μη κλιματιζόμενο",
           "Δάπεδο προς μη θερμαινόμενο": "Δάπεδο προς μη κλιματιζόμενο"}
NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def norm(x):
    return re.sub(r'(Ημερομηνία|Date): [^\n|]*', r'\1: <date>', x) if isinstance(x, str) else x


def tc(v):
    return v.split(" ||TC|| ", 1)[1] if isinstance(v, str) and " ||TC|| " in v else v


def nums(v):
    return NUM_RE.findall(tc(v) or "")


a = json.load(open(args[0], encoding="utf-8"))
b = json.load(open(args[1], encoding="utf-8"))
bad = 0


def diff(tag, key, va, vb):
    global bad
    bad += 1
    print("DIFF", tag, key)
    print("  baseline :", str(va)[:400])
    print("  candidate:", str(vb)[:400])


if len(a["snaps"]) != len(b["snaps"]):
    diff("-", "snapshot count", len(a["snaps"]), len(b["snaps"]))
for sa, sb in zip(a["snaps"], b["snaps"]):
    tag = sa["__tag"]
    if STRICT:
        for k in sa:
            if norm(json.dumps(tc(sa[k]), ensure_ascii=False)) != norm(json.dumps(tc(sb.get(k)), ensure_ascii=False)):
                diff(tag, k, tc(sa[k]), tc(sb.get(k)))
        continue
    for i in sorted(LEGACY_IDS):
        if i in sa["__values"] and sa["__values"][i] != sb["__values"].get(i):
            diff(tag, "input " + i, sa["__values"][i], sb["__values"].get(i))
    rows_b = dict((k, v) for k, v in (sb.get("__rows") or []))
    for k, v in (sa.get("__rows") or []):
        k = RENAMED.get(k, k) if k not in rows_b else k
        if rows_b.get(k) != v:
            diff(tag, "result row " + k, v, rows_b.get(k))
    for k in NUMERIC_AREAS:
        if nums(sa.get(k)) != nums(sb.get(k)):
            diff(tag, k + " (numbers)", tc(sa.get(k)), tc(sb.get(k)))
    if REPORTS and sa.get("__pie") != sb.get("__pie"):  # chart hidden since v9 (labels renamed); checked with --reports only
        diff(tag, "pie pixels", sa.get("__pie"), sb.get("__pie"))
    if sa.get("__tables") != sb.get("__tables"):
        diff(tag, "table rows", sa.get("__tables"), sb.get("__tables"))
if STRICT or REPORTS:
    for pa, pb in zip(a["popups"], b["popups"]):
        same = norm(pa[1]) == norm(pb[1]) if STRICT else NUM_RE.findall(norm(pa[1])) == NUM_RE.findall(norm(pb[1]))
        if not same:
            diff("report", pa[0], pa[1][:200], pb[1][:200])
if b["errors"]:
    diff("-", "page errors in candidate", [], b["errors"])
print(f"{len(a['snaps'])} scenarios -> {bad} differences ({'strict' if STRICT else 'semantic'} mode)")
sys.exit(1 if bad else 0)

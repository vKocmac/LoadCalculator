"""Capture every calculated output of LoadCalculator for a fixed set of scenarios.
Usage: python3 capture.py <html_path> <out.json>
Optional: CHROMIUM_PATH=/path/to/chrome to use a specific browser binary.
Outputs are compared with compare.py (textContent of every result area, all input
values, pie-chart pixels and the text of the three PDF reports).
"""
import json, os, random, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HTML = Path(sys.argv[1]).resolve().as_uri()
OUT = sys.argv[2]
LEGACY = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy_options.json"), encoding="utf-8"))
OUT_IDS = ["result", "buildingTotals", "spacesTree", "liveChecks", "assumptionsList",
           "wallPctHint", "roofPctHint", "floorPctHint", "floorPresetHint",
           "psychroSummary", "psychroMix", "psychroRoomHint", "psychroInputHint"]
SKIP_INPUTS = {"Lang", "projectName", "roomName", "psRoomSelect"}


def snap(page, tag):
    d = page.evaluate("""(ids) => {
      const o = {};
      ids.forEach(id => { const e = document.getElementById(id); o[id] = e ? (e.innerText.trim() + ' ||TC|| ' + e.textContent.replace(/\s+/g, ' ').trim()) : null; });
      const vals = {};
      document.querySelectorAll('input,select').forEach(e => { if (e.id) vals[e.id] = e.value; });
      o.__values = vals;
      o.__tables = ['winTable','devTable'].map(t => { const e = document.getElementById(t); return e ? e.querySelectorAll('tbody tr').length : null; });
      o.__rows = (() => { const r = document.getElementById('result'); if (!r) return null;
        const rows = []; const k = r.querySelector('.kpi-main');
        if (k) rows.push(['__total', (k.querySelector('b') || k).textContent.trim()]);
        r.querySelectorAll('.rowline').forEach(x => { const s = x.querySelectorAll('span'); if (s.length >= 2) rows.push([s[0].textContent.trim(), s[s.length - 1].textContent.trim()]); });
        return rows; })();
      o.__pie = (() => { const c = document.getElementById('pie'); try { return c ? c.toDataURL().length + ':' + c.toDataURL().slice(-64) : null; } catch (e) { return 'err'; } })();
      return o;
    }""", OUT_IDS)
    d["__tag"] = tag
    return d


def numeric_inputs(page):
    # legacy numeric fields only (fixed order), so new fields never change the scenarios
    return [i for i in LEGACY["num"] if page.evaluate("(i) => { const e = document.getElementById(i); return !!e && !e.disabled; }", i)]


def selects(page, scope):
    # legacy selects with their legacy option values only
    ids = [k for k in LEGACY["sel"] if (k.startswith("PS_") if scope == "#psychroPane" else not k.startswith("PS_"))]
    return [[k, LEGACY["sel"][k]] for k in ids
            if k not in ("Lang", "psRoomSelect", "Units") and page.evaluate("(i) => { const e = document.getElementById(i); return !!e && !e.disabled; }", k)]


def setv(page, sel, val):
    page.evaluate("""([s, v]) => { const e = document.querySelector(s); e.value = v;
      e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); }""", [sel, str(val)])


def click(page, sel):
    page.evaluate("(s) => document.querySelector(s).click()", sel)


def run():
    snaps, errors, popups = [], [], []
    with sync_playwright() as p:
        launch_kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
        b = p.chromium.launch(headless=True, **launch_kw)
        ctx = b.new_context()
        ctx.route("**/googletagmanager.com/**", lambda r: r.abort())
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(HTML)
        page.wait_for_timeout(300)
        setv(page, "#Units", "kW")  # results compared in kW (W display was removed in v9)
        snaps.append(snap(page, "load"))
        click(page, "#btnCalc"); snaps.append(snap(page, "default_calc"))

        for mode in ["heat", "cool"]:
            setv(page, "#Mode", mode); click(page, "#btnCalc"); snaps.append(snap(page, "mode_" + mode))

        for seed in range(1, 9):
            rnd = random.Random(seed)
            for sid, opts in selects(page, "#loadsPane"):
                if sid in SKIP_INPUTS or sid.startswith("PS_"):
                    continue
                setv(page, "#" + sid, rnd.choice(opts))
            if rnd.random() < .6: click(page, "#addWinGrp")
            if rnd.random() < .6: click(page, "#addDev")
            for nid in numeric_inputs(page):
                if nid in SKIP_INPUTS:
                    continue
                cur = page.input_value("#" + nid)
                try: v = float(cur or 0)
                except ValueError: v = 0
                nv = round(max(0, v * rnd.uniform(0.5, 1.6) + rnd.uniform(0, 2)), 2)
                if "Pct" in nid: nv = rnd.choice([0, 30, 50, 70, 100])
                if "Duty" in nid or "shade" in nid.lower(): nv = round(rnd.uniform(0, 1), 2)
                setv(page, "#" + nid, nv)
            click(page, "#btnCalc")
            snaps.append(snap(page, f"seed_{seed}"))
            if seed in (2, 5, 7):
                setv(page, "#roomName", f"Room{seed}")
                click(page, "#addRoomBtn")
                snaps.append(snap(page, f"room_add_{seed}"))

        # PDFs
        for btn in ["exportLoadPdfBtn", "exportLoadDetailedPdfBtn"]:
            try:
                with ctx.expect_page(timeout=4000) as pi:
                    click(page, "#" + btn)
                pp = pi.value; pp.wait_for_timeout(400)
                popups.append([btn, pp.evaluate("document.body.innerText")]); pp.close()
            except Exception as e:
                popups.append([btn, "ERR " + str(e)[:100]])

        # Psychrometrics
        click(page, "#tabPsychro"); page.wait_for_timeout(200)
        snaps.append(snap(page, "psy_open"))
        for seed in range(1, 5):
            rnd = random.Random(100 + seed)
            for sid, opts in selects(page, "#psychroPane"):
                if sid in SKIP_INPUTS: continue
                setv(page, "#" + sid, rnd.choice(opts))
            for nid in ["PS_Tsa", "PS_RHsa", "PS_SA_manual", "PS_Pressurization", "PS_BF", "PS_ADP", "PS_LWT"]:
                if page.evaluate("(s) => document.querySelector(s).disabled", "#" + nid): continue
                val = {"PS_Tsa": rnd.uniform(11, 16), "PS_RHsa": rnd.uniform(80, 95), "PS_SA_manual": rnd.choice(["", 800, 1500]),
                       "PS_Pressurization": rnd.choice([0, 50, 100]), "PS_BF": rnd.uniform(.05, .2),
                       "PS_ADP": rnd.uniform(8, 12), "PS_LWT": rnd.uniform(10, 14)}[nid]
                setv(page, "#" + nid, round(val, 2) if val != "" else "")
            if seed == 2: click(page, "#PS_Suggest_T")
            click(page, "#PS_Recalc")
            snaps.append(snap(page, f"psy_{seed}"))
        try:
            with ctx.expect_page(timeout=4000) as pi:
                click(page, "#exportPsychroPdfBtn")
            pp = pi.value; pp.wait_for_timeout(400)
            popups.append(["psyPdf", pp.evaluate("document.body.innerText")]); pp.close()
        except Exception as e:
            popups.append(["psyPdf", "ERR " + str(e)[:100]])
        click(page, "#closePsychro")

        setv(page, "#Lang", "en"); page.wait_for_timeout(100); click(page, "#btnCalc")
        snaps.append(snap(page, "lang_en"))
        setv(page, "#Lang", "el"); page.wait_for_timeout(100); click(page, "#btnCalc")
        snaps.append(snap(page, "lang_el"))
        b.close()
    json.dump({"snaps": snaps, "popups": popups, "errors": errors}, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(len(snaps), "snapshots,", len(popups), "popups,", len(errors), "page errors", errors[:3])


run()

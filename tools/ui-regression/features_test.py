"""Checks for the v9 features against hand calculations.
Usage: python3 features_test.py <index.html>      (CHROMIUM_PATH optional)
Exit code 0 = all checks passed.
"""
import json, os, re, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = Path(sys.argv[1]).resolve().as_uri()
KW = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
RHO, CP, BTU = 1.2, 1.006, 3.412142
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def setv(page, sel, val):
    page.evaluate("""([s, v]) => { const e = document.querySelector(s); e.value = v;
      e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); }""", [sel, str(val)])


def click(page, sel):
    page.evaluate("(s) => document.querySelector(s).click()", sel)


def rows(page):
    """result rows in W (reads BTU/h values, 1 BTU/h resolution)."""
    data = page.evaluate("""() => { const o = {}; const r = document.getElementById('result');
      const k = r.querySelector('.kpi-main b'); o.__total = k ? k.textContent : '';
      r.querySelectorAll('.rowline').forEach(x => { const s = x.querySelectorAll('span'); o[s[0].textContent.trim()] = s[s.length-1].textContent.trim(); });
      return o; }""")
    out = {}
    for k, v in data.items():
        m = re.match(r"^([\d .]+)\s*BTU/h", v)
        if m:
            out[k] = float(m.group(1).replace(" ", "")) / BTU
        else:
            out[k] = v
    return out


def base_room(page):
    """a simple, fully known room"""
    setv(page, "#Units", "BTU/h")
    setv(page, "#Mode", "cool")
    setv(page, "#city", "Custom")
    for k, v in {"GeoMode": "dims", "L": 5, "W": 4, "H": 3, "Tin": 24, "RHin": 50, "Tout": 35, "RHout": 40, "Tadj": 40,
                 "LwallExt": 9, "LwallUnh": 0, "UwallPreset": "0.70", "UwallUnhPreset": "same", "UwinPreset": "2.8", "SHGCpreset": "0.70",
                 "UroofPreset": "0.35", "RoofPctExt": 0, "RoofPctUnh": 0, "RoofPctHeated": 100, "UfloorPreset": "g|0.50",
                 "FloorPctGround": 100, "FloorPctUnh": 0, "FloorPctHeated": 0, "ACHinf": "0.7", "ACHnat": "0", "InfSource": "out",
                 "MechMode": "flow", "Qmech": 0, "ACHmech": 0, "OaMode": "direct", "InfSource": "out", "nPeople": 0, "LightingWm2": 0, "EquipWm2": 0}.items():
        setv(page, "#" + k, v)
    page.evaluate("""() => { document.querySelector('#winTable tbody').innerHTML = ''; document.querySelector('#devTable tbody').innerHTML = ''; }""")
    click(page, "#btnCalc")


def near(a, b, tol=1.0):
    return isinstance(a, float) and abs(a - b) <= tol


def total_w(page):
    return rows(page)["__total"]


with sync_playwright() as p:
    b = p.chromium.launch(headless=True, **KW)
    ctx = b.new_context(accept_downloads=True)
    ctx.route("**/googletagmanager.com/**", lambda r: r.abort())
    ctx.route("**/fonts.g*/**", lambda r: r.abort())
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(URL)
    page.wait_for_timeout(400)

    # 1. area mode == L x W
    base_room(page)
    t_dims = total_w(page)
    setv(page, "#GeoMode", "area"); setv(page, "#Afloor", 20); click(page, "#btnCalc")
    t_area = total_w(page)
    check("area mode gives same result as L×W (20 m²)", near(t_area, t_dims, 1.0), f"{t_dims:.1f} vs {t_area:.1f} W")
    # area mode: wall length is not clipped by a perimeter
    setv(page, "#GeoMode", "area"); setv(page, "#LwallExt", 30); click(page, "#btnCalc")
    check("area mode keeps a wall length longer than 2(L+W)", page.input_value("#LwallExt") == "30", page.input_value("#LwallExt"))
    setv(page, "#GeoMode", "dims"); setv(page, "#LwallExt", 9)

    # 2. walls: external wall only  9 m x 3 m x 0.70 x 11 K
    base_room(page)
    r = rows(page)
    check("external wall U·A·ΔT", near(r.get("Εξωτερικοί τοίχοι"), 9 * 3 * 0.70 * 11, 1.0), f"{r.get('Εξωτερικοί τοίχοι')}")

    # 3. wall to unconditioned space with its own U (custom 0.5), Tadj 40
    setv(page, "#LwallExt", 0); setv(page, "#LwallUnh", 5); setv(page, "#UwallUnhPreset", "custom"); setv(page, "#UwallUnhCustom", 0.5); click(page, "#btnCalc")
    r = rows(page)
    check("wall to unconditioned uses its own U", near(r.get("Τοίχοι προς μη κλιματιζόμενο"), 5 * 3 * 0.5 * 16, 1.0), f"{r.get('Τοίχοι προς μη κλιματιζόμενο')}")

    # 4. internal window: 2 m², U 2.8, ΔT 16, no solar, deducted from unconditioned wall
    page.evaluate("() => document.getElementById('addWinGrp').click()")
    page.evaluate("""() => { const tr = document.querySelector('#winTable tbody tr:last-child');
      tr.querySelector('.wg-orient').value = 'UNH'; tr.querySelector('.wg-area').value = '2'; tr.querySelector('.wg-shade').value = '1'; }""")
    click(page, "#btnCalc")
    r = rows(page)
    check("internal window conduction with Tadj", near(r.get("Υαλοστάσια προς μη κλιματιζόμενο"), 2 * 2.8 * 16, 1.0), f"{r.get('Υαλοστάσια προς μη κλιματιζόμενο')}")
    check("internal window: no solar gain", "Ηλιακά κέρδη υαλοστασίων" not in r, "")
    check("internal window area deducted from unconditioned wall", near(r.get("Τοίχοι προς μη κλιματιζόμενο"), (5 * 3 - 2) * 0.5 * 16, 1.0), f"{r.get('Τοίχοι προς μη κλιματιζόμενο')}")

    # 5. shading factor 0 = no solar (was treated as 1 before)
    base_room(page)
    page.evaluate("() => document.getElementById('addWinGrp').click()")
    page.evaluate("""() => { const tr = document.querySelector('#winTable tbody tr:last-child');
      tr.querySelector('.wg-orient').value = 'S'; tr.querySelector('.wg-area').value = '2'; tr.querySelector('.wg-shade').value = '0'; }""")
    click(page, "#btnCalc")
    r = rows(page)
    check("shading factor 0 gives no solar gain", "Ηλιακά κέρδη υαλοστασίων" not in r, str(r.get("Ηλιακά κέρδη υαλοστασίων")))
    page.evaluate("() => { document.querySelector('#winTable tbody tr:last-child .wg-shade').value = '0.5'; }")
    click(page, "#btnCalc")
    r = rows(page)
    check("shading 0.5: 2 m² × 0.70 × 400 × 0.5", near(r.get("Ηλιακά κέρδη υαλοστασίων"), 2 * 0.70 * 400 * 0.5, 1.0), f"{r.get('Ηλιακά κέρδη υαλοστασίων')}")

    # 6. device duty 0 = off; equipment W/m²
    base_room(page)
    page.evaluate("() => document.getElementById('addDev').click()")
    page.evaluate("() => { document.querySelector('#devTable tbody tr:last-child .dv-duty').value = '0'; }")
    setv(page, "#EquipWm2", 10)
    click(page, "#btnCalc")
    r = rows(page)
    check("duty 0 device adds nothing; 10 W/m² × 20 m² = 200 W", near(r.get("Εξοπλισμός"), 200, 1.0), f"{r.get('Εξοπλισμός')}")

    # 7. infiltration from unconditioned space
    base_room(page)
    V = 5 * 4 * 3
    m = 0.7 * V / 3600 * RHO
    click(page, "#btnCalc")
    s_out = rows(page).get("Αερισμός αισθητό")
    check("infiltration from outdoors (ΔT 11 K)", near(s_out, m * CP * 11 * 1000, 1.0), f"{s_out}")
    setv(page, "#InfSource", "adj"); click(page, "#btnCalc")
    s_adj = rows(page).get("Αερισμός αισθητό")
    check("infiltration from unconditioned space (ΔT 16 K)", near(s_adj, m * CP * 16 * 1000, 1.0), f"{s_adj}")

    # 8. units BTU/h vs kW, specific load row
    base_room(page)
    tb = total_w(page)
    setv(page, "#Units", "kW"); click(page, "#btnCalc")
    tk = float(re.match(r"([\d.]+)", page.evaluate("() => document.querySelector('#result .kpi-main b').textContent")).group(1)) * 1000
    check("BTU/h and kW show the same load", abs(tb - tk) <= 6, f"{tb:.0f} W vs {tk:.0f} W")
    spec = page.evaluate("""() => Array.from(document.querySelectorAll('#result .rowline')).map(x => x.textContent).find(t => t.includes('Ειδικό'))""")
    check("specific load row shows W/m² and BTU/h·m²", spec and "W/m²" in spec and "BTU/h·m²" in spec, spec)
    options = page.evaluate("() => Array.from(document.getElementById('Units').options).map(o => o.value)")
    check("units offered: kW and BTU/h only", options == ["kW", "BTU/h"], str(options))

    # 9. rooms keep their own windows/devices (pre-v9 they did not)
    base_room(page)
    page.evaluate("() => document.getElementById('addWinGrp').click()")
    page.evaluate("() => { const tr = document.querySelector('#winTable tbody tr:last-child'); tr.querySelector('.wg-orient').value = 'W'; tr.querySelector('.wg-area').value = '4'; }")
    click(page, "#btnCalc")
    t_room1 = total_w(page)
    setv(page, "#roomName", "Room A"); click(page, "#addRoomBtn")
    page.evaluate("() => { document.querySelector('#winTable tbody').innerHTML = ''; }")
    click(page, "#btnCalc")
    t_room2 = total_w(page)
    setv(page, "#roomName", "Room B"); click(page, "#addRoomBtn")
    page.evaluate("""() => { const rows = document.querySelectorAll('#spacesTree .space-row'); rows[0].click(); }""")
    page.wait_for_timeout(200)
    t_back = total_w(page)
    nwin = page.evaluate("() => document.querySelectorAll('#winTable tbody tr').length")
    check("re-selecting room A restores its windows", nwin == 1 and near(t_back, t_room1, 1.0), f"{nwin} rows, {t_back:.0f} vs {t_room1:.0f} W (room B {t_room2:.0f} W)")

    # 9b. the other season of a saved room: only from a real calculation (no estimate)
    tree = page.evaluate("() => document.getElementById('spacesTree').innerText")
    check("saved in cooling: heat losses shown as — until calculated", tree.count("Απώλειες: —") == 2, tree.replace("\n", " | ")[:200])
    base_room(page)
    setv(page, "#city", "Athens|36|40"); click(page, "#btnCalc")
    page.evaluate("() => document.getElementById('addWinGrp').click()")
    page.evaluate("() => { const tr = document.querySelector('#winTable tbody tr:last-child'); tr.querySelector('.wg-orient').value = 'N'; tr.querySelector('.wg-area').value = '4'; }")
    click(page, "#btnCalc")
    cool_before = total_w(page)
    setv(page, "#roomName", "Athens room"); click(page, "#addRoomBtn")
    # select it, switch to heating, calculate
    page.evaluate("() => { const r = document.querySelectorAll('#spacesTree .space-row'); r[r.length - 1].click(); }")
    setv(page, "#Mode", "heat"); click(page, "#btnCalc")
    last = page.evaluate("() => { const r = document.querySelectorAll('#spacesTree .space-row'); return r[r.length - 1].innerText; }")
    heat = re.search(r"Απώλειες: ([\d\u202f]+) BTU/h", last)
    cool = re.search(r"Ψύξη: ([\d\u202f]+) BTU/h", last)
    exp = (27 - 4) * 0.70 * 20 + 4 * 2.8 * 20 + 0.7 * 60 / 3600 * RHO * CP * 20 * 1000 + 20 * 0.5 * 10
    got = float(heat.group(1).replace("\u202f", "")) / BTU if heat else None
    gotc = float(cool.group(1).replace("\u202f", "")) / BTU if cool else None
    check("heat losses after calculating the room in heating (winter Athens)", got and abs(got - exp) < 2, f"{got} vs {exp:.1f} W")
    check("cooling result of the room is kept after the heating calculation", gotc and abs(gotc - cool_before) < 2, f"{gotc} vs {cool_before:.1f} W")
    setv(page, "#H", 3.5); click(page, "#btnCalc")
    last = page.evaluate("() => { const r = document.querySelectorAll('#spacesTree .space-row'); return r[r.length - 1].innerText; }")
    check("changing the geometry drops the other season (needs recalculation)", "Ψύξη: —" in last, last.replace("\n", " | "))
    setv(page, "#Mode", "cool")

    # 9c. fresh air through AHU: excluded from room load, shown separately
    base_room(page)
    setv(page, "#Qmech", 200); click(page, "#btnCalc")
    direct = rows(page)
    setv(page, "#OaMode", "ahu"); click(page, "#btnCalc")
    ahu = rows(page)
    m_oa = 200 / 3600 * RHO
    check("AHU: room ventilation = infiltration only", near(ahu.get("Αερισμός αισθητό"), 0.7 * 60 / 3600 * RHO * CP * 11 * 1000, 1.0), f"{ahu.get('Αερισμός αισθητό')}")
    check("AHU: fresh-air sensible shown separately", near(ahu.get("Αισθητό νωπού"), m_oa * CP * 11 * 1000, 1.0), f"{ahu.get('Αισθητό νωπού')}")
    check("AHU: room + fresh air = direct total", near(ahu.get("Χώρος + νωπός"), direct["__total"], 2.0), f"{ahu.get('Χώρος + νωπός')} vs {direct['__total']:.1f}")

    # 9d. per-row type: an opaque steel door on the west, no solar, own U
    base_room(page)
    page.evaluate("() => document.getElementById('addWinGrp').click()")
    page.evaluate("""() => { const tr = document.querySelector('#winTable tbody tr:last-child'); tr.querySelector('.wg-orient').value = 'W';
      tr.querySelector('.wg-area').value = '2'; tr.querySelector('.wg-u').value = '5.8'; tr.querySelector('.wg-shgc').value = '0'; }""")
    click(page, "#btnCalc")
    r = rows(page)
    check("door row: conduction with its own U (2 m² × 5.8 × 11 K)", near(r.get("Υαλοστάσια αγωγιμότητα"), 2 * 5.8 * 11, 1.0), f"{r.get('Υαλοστάσια αγωγιμότητα')}")
    check("door row with SHGC 0: no solar gain", "Ηλιακά κέρδη υαλοστασίων" not in r, "")

    # 9e. old presets with far too optimistic U are migrated
    page.evaluate("""() => { const o = document.getElementById('UroofPreset'); const x = document.createElement('option'); x.value = '1.00'; o.appendChild(x); }""")
    opts = page.evaluate("() => Array.from(document.getElementById('UfloorPreset').options).map(o => o.value)")
    check("removed presets are gone from the lists", "p|1.50" not in opts, "")

    # 10. project file round trip
    setv(page, "#projectName", "TEST-01 Αποθήκη")
    with page.expect_download() as dl:
        click(page, "#lcSaveProj")
    path = dl.value.path()
    html = Path(path).read_text(encoding="utf-8")
    check("project file name ends with .lcproj.html", dl.value.suggested_filename.endswith(".lcproj.html"), dl.value.suggested_filename)
    state = json.loads(re.search(r'<script id="lcproj" type="application/json">(.*?)</script>', html, re.S).group(1))
    before = page.evaluate("""() => ({ tree: document.getElementById('spacesTree').innerText, total: document.getElementById('buildingTotals').innerText })""")
    page2 = b.new_context().new_page()
    page2.route("**/googletagmanager.com/**", lambda r: r.abort())
    page2.on("pageerror", lambda e: errors.append("page2: " + str(e)))
    tmp = Path(tempfile.mkdtemp()) / "proj.lcproj.html"
    tmp.write_text(html.replace(json.dumps(URL.split('#')[0])[1:-1], URL.split('#')[0]), encoding="utf-8")
    page2.goto(tmp.as_uri())
    page2.wait_for_timeout(1500)
    after = page2.evaluate("""() => ({ tree: document.getElementById('spacesTree').innerText, total: document.getElementById('buildingTotals').innerText, name: document.getElementById('projectName').value, hash: location.hash })""")
    check("double-clicking the project file opens the tool filled in", after["name"] == "TEST-01 Αποθήκη" and after["tree"] == before["tree"] and after["total"] == before["total"], json.dumps(after, ensure_ascii=False)[:200])
    check("project link is removed from the address bar after opening", after["hash"] == "", after["hash"][:30])

    # 11. nothing kept in the browser: reopening without the file starts a new study; guide progress travels in the file
    keys = page2.evaluate("() => Object.keys(localStorage)")
    page2.reload(); page2.wait_for_timeout(1200)
    again = page2.evaluate("""() => ({ tree: document.getElementById('spacesTree').innerText, name: document.getElementById('projectName').value, note: !document.getElementById('lcNote').hidden })""")
    check("no data kept in the browser; reopening the page starts empty", keys == [] and again["name"] == "" and "Room A" not in again["tree"] and not again["note"], json.dumps({"keys": keys, **again}, ensure_ascii=False)[:160])
    check("project file carries the guided-flow progress", isinstance(state.get("guide"), dict), str(state.get("guide"))[:80])

    # 12. reports open without errors and show the building total
    for btn in ["exportLoadPdfBtn", "exportLoadDetailedPdfBtn"]:
        with ctx.expect_page() as pi:
            click(page, "#" + btn)
        rp = pi.value
        rp.wait_for_timeout(300)
        txt = rp.evaluate("document.body.innerText")
        tot = page.evaluate("() => document.getElementById('buildingTotals').innerText")
        cool = re.search(r"([\d .]+ BTU/h)", tot)
        check(f"{btn}: report contains the building cooling total", cool and cool.group(1) in txt, cool.group(1) if cool else tot)
        if btn == "exportLoadDetailedPdfBtn":
            txt2 = rp.evaluate("document.body.textContent"); check("detailed report contains input data", "Δεδομένα εισόδου" in txt2 and "Κουφώματα" in txt2, "")
        rp.close()

    # 14. psychrometrics: direct fresh air not mixed at the coil; coil sheet water flow
    base_room(page)
    setv(page, "#Qmech", 200); setv(page, "#Units", "kW"); click(page, "#btnCalc")
    click(page, "#tabPsychro"); page.wait_for_timeout(200)
    setv(page, "#PS_SA_RH_mode", "manual"); setv(page, "#PS_RHsa", 92); setv(page, "#PS_Tsa", 14)
    click(page, "#PS_Recalc"); page.wait_for_timeout(200)
    def coil_kw():
        return page.evaluate("() => { const m = document.getElementById('psychroMix').innerText.match(/Ισχύς στοιχείου\\s*([\\d.]+) kW/); return m ? parseFloat(m[1]) : null; }")
    q_direct = coil_kw()
    mix = page.evaluate("() => document.getElementById('psychroMix').innerText")
    setv(page, "#OaMode", "ahu"); click(page, "#btnCalc"); click(page, "#PS_Recalc"); page.wait_for_timeout(200)
    q_ahu = coil_kw()
    setv(page, "#OaMode", "direct"); click(page, "#btnCalc"); click(page, "#PS_Recalc"); page.wait_for_timeout(200)
    check("fresh air always in the coil mix; same coil for direct and AHU (no double counting)", "200 / " in mix and q_direct and q_ahu and abs(q_direct - q_ahu) <= 0.011, f"direct {q_direct} kW vs AHU {q_ahu} kW")
    sheet = page.evaluate("() => document.getElementById('coilSheet').innerText")
    q = page.evaluate("() => { const m = document.getElementById('psychroMix').innerText.match(/Ισχύς στοιχείου\\s*([\\d.]+) kW/); return m ? parseFloat(m[1]) : null; }")
    vw = re.search(r"Παροχή νερού\s*([\d.]+) m³/h", sheet)
    exp_vw = q * 1000 / (4186 * 5) * 3.6 if q else None
    check("coil sheet: water flow = Q / (4.186 · ΔT)", vw and exp_vw and abs(float(vw.group(1)) - exp_vw) < 0.02, f"{vw.group(1) if vw else None} vs {exp_vw}")
    check("coil sheet: rows, face area and feasibility shown", "Σειρές" in sheet and "Επιφάνεια μετώπου" in sheet and "Πρώτος έλεγχος" in sheet, "")
    setv(page, "#OaMode", "ahu"); click(page, "#btnCalc"); click(page, "#PS_Recalc"); page.wait_for_timeout(200)
    mix2 = page.evaluate("() => document.getElementById('psychroMix').innerText")
    frac = re.search(r"Ποσοστό OA: ([\d.]+)%", mix2)
    check("AHU fresh air: OA fraction never above 100%", frac and float(frac.group(1)) <= 100.0001, frac.group(1) if frac else mix2[:100])
    setv(page, "#OaMode", "direct"); click(page, "#btnCalc")
    setv(page, "#PS_ViewMode", "expert"); setv(page, "#PS_Tsa", 10); setv(page, "#PS_SA_RH_mode", "sat"); click(page, "#PS_Recalc"); page.wait_for_timeout(200)
    sheet = page.evaluate("() => document.getElementById('coilSheet').innerText")
    check("coil sheet: saturated supply flagged as not achievable", "ΜΗ εφικτό" in sheet, sheet[:120].replace("\n", " | "))
    # simple view explains results and checks room humidity
    setv(page, "#PS_ViewMode", "simple"); setv(page, "#PS_Tsa", 14); click(page, "#PS_Recalc"); page.wait_for_timeout(200)
    mix = page.evaluate("() => document.getElementById('psychroMix').innerText")
    status = page.evaluate("() => document.getElementById('psyStatus').innerText")
    check("simple view: humidity check and loads status shown", ("υγρασία του χώρου" in mix) and ("Qs" in status) and ("Νωπός" in status), status.replace("\n", " | ")[:160])
    click(page, "#closePsychro")

    # 15. AHU grouping: coil for the sum of the AHU's rooms
    base_room(page); setv(page, "#Units", "kW"); setv(page, "#OaMode", "ahu"); setv(page, "#Qmech", 100); setv(page, "#AhuName", "ΚΚΜ-1"); click(page, "#btnCalc")
    setv(page, "#roomName", "AHU room 1"); click(page, "#addRoomBtn")
    setv(page, "#nPeople", 4); click(page, "#btnCalc")
    setv(page, "#roomName", "AHU room 2"); click(page, "#addRoomBtn")
    qs_sum = page.evaluate("""() => Array.from(document.querySelectorAll('#spacesTree .space-row')).filter(r => r.innerText.includes('ΚΚΜ: ΚΚΜ-1'))
      .map(r => parseFloat((r.innerText.match(/Qs=([\\d.]+) kW/) || [0, 0])[1])).reduce((a, b) => a + b, 0)""")
    click(page, "#tabPsychro"); page.wait_for_timeout(200)
    has = page.evaluate("() => Array.from(document.getElementById('psRoomSelect').options).some(o => o.value === 'ahu:ΚΚΜ-1')")
    setv(page, "#psRoomSelect", "ahu:ΚΚΜ-1"); page.wait_for_timeout(300)
    mix = page.evaluate("() => document.getElementById('psychroMix').innerText")
    m = re.search(r"Qs ([\d.]+) kW", mix)
    n_rooms = page.evaluate("() => Array.from(document.querySelectorAll('#spacesTree .space-row')).filter(r => r.innerText.includes('ΚΚΜ: ΚΚΜ-1')).length")
    check("AHU group: coil option, summed room sensible and fresh air", has and m and abs(float(m.group(1)) - qs_sum) < 0.02 and f"νωπός {n_rooms * 100} m³/h" in mix, f"{m.group(1) if m else None} vs {qs_sum:.2f}; {has}; " + mix[:300].replace("\n", " | "))
    click(page, "#closePsychro")

    # 16. guided flow: going back keeps the ticks and "Next" jumps to the first unchecked section
    page.evaluate("window.scrollTo(0,0)")
    for k in range(3):
        page.evaluate("() => { const b = document.querySelector('#loadsMain > .section.lc-active .lc-next button'); if (b) b.click(); }")
        page.wait_for_timeout(150)
    page.focus("#loadsMain > .section:nth-of-type(2) input:not([disabled]), #L")
    page.wait_for_timeout(150)
    page.evaluate("() => { const b = document.querySelector('#loadsMain > .section.lc-active .lc-next button'); if (b) b.click(); }")
    page.wait_for_timeout(200)
    st = page.evaluate("""() => Array.from(document.querySelectorAll('#loadsMain > .section')).map(s => (s.classList.contains('lc-done') ? 'D' : '-') + (s.classList.contains('lc-active') ? 'A' : ''))""")
    check("guided flow: ticks kept, Next goes to first unchecked section", st[:3] == ["D", "D", "D"] and st[3] == "-A", str(st))

    # 17. engineer name and per-m² column in the detailed report
    setv(page, "#engineerName", "Κ. Μηχανικός, ΜΜ")
    with ctx.expect_page() as pi:
        click(page, "#exportLoadDetailedPdfBtn")
    rp = pi.value; rp.wait_for_timeout(300)
    t = rp.evaluate("document.body.textContent")
    css = rp.evaluate("Array.from(document.styleSheets).map(x => Array.from(x.cssRules).map(r => r.cssText).join(' ')).join(' ')")
    check("report: engineer name, W/m² and BTU/h·m² per component, bars printable", "Κ. Μηχανικός, ΜΜ" in t and "BTU/h·m²" in t and "print-color-adjust" in css, "")
    rp.close()

    # 18. mechanical ventilation: only the selected method counts; typing selects the method
    base_room(page); setv(page, "#MechMode", "ach"); setv(page, "#ACHmech", 0)
    page.evaluate("() => { document.getElementById('Qmech').value = '500'; }")  # value without typing
    click(page, "#btnCalc")
    r_ach = rows(page)
    check("ACH method: the m³/h field is not counted", near(r_ach.get("Αερισμός αισθητό"), 0.7 * 60 / 3600 * RHO * CP * 11 * 1000, 1.0), f"{r_ach.get('Αερισμός αισθητό')}")
    page.fill("#Qmech", ""); page.type("#Qmech", "500")                    # real typing
    click(page, "#btnCalc")
    r_flow = rows(page)
    exp = (0.7 * 60 / 3600 + 500 / 3600) * RHO * CP * 11 * 1000
    check("typing an airflow switches the method to m³/h and counts it", page.input_value("#MechMode") == "flow" and near(r_flow.get("Αερισμός αισθητό"), exp, 1.0), f"{page.input_value('#MechMode')} {r_flow.get('Αερισμός αισθητό')} vs {exp:.1f}")
    click(page, "#tabPsychro"); page.wait_for_timeout(200)
    setv(page, "#psRoomSelect", ""); click(page, "#loadRoomBtn"); page.wait_for_timeout(200)
    st = page.evaluate("() => document.getElementById('psyStatus').innerText")
    check("psychrometrics sees the same fresh air (500 m³/h)", "500 m³/h" in st, st.replace("\n", " | ")[:160])
    click(page, "#closePsychro")

    # 13. Greek everywhere in the loads form
    allowed = set("""U SHGC ACH RH CAD LED PVC PU PIR XPS EPS ETICS CNC UPS POS IT kW BTU h W m Qs Ql SHR TV PC D LED T8 low-e low Ytong sandwich
      rack Switch switch Plotter espresso high-bay kVA inverter P η kg A B N S E NE NW SE SW Β Α Ν Δ ΒΑ ΒΔ ΝΑ ΝΔ g p x ASHRAE Fundamentals Argon POS PDF Rack Ug Uw cm mm laser PU AHU""".split())
    texts = page.evaluate("""() => Array.from(document.querySelectorAll('#loadsMain label, #loadsMain h2, #loadsMain th, #loadsMain button, #loadsMain option, #loadsMain optgroup, #loadsMain .desc, .side h3, .side summary, .side button, .side label, #tabsBar button'))
      .map(e => e.tagName === 'OPTGROUP' ? e.label : e.textContent)""")
    leftovers = set()
    for t in texts:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]+", t):
            if w not in allowed:
                leftovers.add(w)
    check("Greek UI: no English words left in the loads form", not leftovers, ", ".join(sorted(leftovers)))

    check("no JavaScript errors", not errors, "; ".join(errors)[:300])
    b.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)

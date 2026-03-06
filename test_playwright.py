from playwright.sync_api import sync_playwright
from pathlib import Path
import re
import random
import json
import os

TARGET_HTML = os.environ.get("TARGET_HTML", r"C:\Users\nonam\Desktop\codex load calculator\LoadCalculator - coil.html")
HTML = Path(TARGET_HTML).resolve()
URL = HTML.as_uri()


def parse_total_w(page):
    txt = page.locator("#result .kpi b").first.inner_text().strip()
    m_kw = re.search(r"([-+]?\d+(?:\.\d+)?)\s*kW", txt)
    if m_kw:
        return float(m_kw.group(1)) * 1000.0
    m_w = re.search(r"([-+]?\d+(?:\.\d+)?)\s*W", txt)
    if m_w:
        return float(m_w.group(1))
    raise AssertionError(f"Cannot parse total from: {txt!r}")


def set_num(page, id_, val):
    el = page.locator(f"#{id_}")
    el.fill("")
    el.fill(str(val))


def calc(page):
    page.locator("#btnCalc").click()


def run():
    results = []
    bugs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page_errors = []
        console_errors = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)
        page.on("console", on_console)

        page.goto(URL)
        page.wait_for_timeout(200)

        # 1) Smoke: default calculation finite positive
        t0 = parse_total_w(page)
        ok1 = t0 > 0 and t0 < 1e7
        results.append(("smoke_default_total", ok1, {"total_w": t0}))
        if not ok1:
            bugs.append("Default total is not finite/positive")

        # 2) Units consistency W <-> kW
        page.select_option("#Units", "W")
        calc(page)
        tw = parse_total_w(page)
        page.select_option("#Units", "kW")
        calc(page)
        tkw = parse_total_w(page)
        ratio_err = abs(tw - tkw)
        ok2 = ratio_err < max(3.0, 0.02 * tw)
        results.append(("units_consistency", ok2, {"W_mode": tw, "kW_mode_as_W": tkw, "abs_diff": ratio_err}))
        if not ok2:
            bugs.append("Units conversion inconsistency W vs kW")

        # 3) City/mode updates temps
        page.select_option("#Mode", "heat")
        page.select_option("#city", "Athens|36|40")
        page.wait_for_timeout(100)
        tin_h = float(page.input_value("#Tin"))
        tout_h = float(page.input_value("#Tout"))
        ok3 = abs(tin_h - 22.0) < 0.1 and abs(tout_h - 2.0) < 0.1
        results.append(("mode_city_setpoints_heat", ok3, {"Tin": tin_h, "Tout": tout_h}))
        if not ok3:
            bugs.append("Mode/city setpoint propagation broken in heating")

        # 4) Custom U toggles enabled state
        page.select_option("#UwallPreset", "custom")
        uwall_disabled = page.locator("#UwallCustom").is_disabled()
        ok4 = (uwall_disabled is False)
        results.append(("custom_toggle_enable", ok4, {"UwallCustom_disabled": uwall_disabled}))
        if not ok4:
            bugs.append("Custom toggle does not enable custom U input")

        # restore cooling baseline
        page.select_option("#Mode", "cool")
        page.select_option("#city", "Athens|36|40")
        page.select_option("#Units", "W")
        calc(page)
        base = parse_total_w(page)

        # 5) Add high-solar windows increases cooling load
        page.locator("#addWinGrp").click()
        last = page.locator("#winTable tbody tr").last
        last.locator(".wg-orient").select_option("W")
        last.locator(".wg-area").fill("20")
        last.locator(".wg-shade").fill("1")
        calc(page)
        after_win = parse_total_w(page)
        ok5 = after_win > base
        results.append(("window_area_increases_load", ok5, {"base": base, "after": after_win, "delta": after_win-base}))
        if not ok5:
            bugs.append("Increasing high-solar window area did not increase load")

        # 6) Mech mode flow should react to Qmech
        page.select_option("#MechMode", "flow")
        set_num(page, "Qmech", 1500)
        calc(page)
        flow_total = parse_total_w(page)
        set_num(page, "Qmech", 0)
        calc(page)
        flow_zero = parse_total_w(page)
        ok6 = flow_total > flow_zero
        results.append(("mech_flow_effect", ok6, {"with_flow": flow_total, "no_flow": flow_zero, "delta": flow_total-flow_zero}))
        if not ok6:
            bugs.append("Mechanical flow (m3/h) has no effect on load")

        # 7) Psychrometric suggest T should not throw errors
        page.locator("#tabPsychro").click()
        page.wait_for_timeout(100)
        page.locator("#PS_SA_manual").fill("1200")
        before_tsa = page.input_value("#PS_Tsa")
        page.locator("#PS_Suggest_T").click()
        page.wait_for_timeout(120)
        after_tsa = page.input_value("#PS_Tsa")
        error_hit = any("Hin" in e or "R.Hin" in e for e in page_errors + console_errors)
        ok7 = (not error_hit)
        results.append(("psychro_suggest_no_exception", ok7, {
            "before_tsa": before_tsa,
            "after_tsa": after_tsa,
            "page_errors": page_errors[-3:],
            "console_errors": console_errors[-3:]
        }))
        if not ok7:
            bugs.append("Psychrometric Suggest T throws exception (likely wrong field name)")

        # 8) Envelope percentage controls: cap at 100% and affect result when distribution changes
        page.locator("#closePsychro").click()
        page.wait_for_timeout(50)
        page.select_option("#Mode", "cool")
        calc(page)
        s0 = parse_total_w(page)

        # Attempt invalid >100% totals; UI should auto-limit active field.
        set_num(page, "RoofPctExt", 100)
        set_num(page, "RoofPctUnh", 30)
        set_num(page, "RoofPctHeated", 40)
        set_num(page, "FloorPctGround", 100)
        set_num(page, "FloorPctUnh", 20)
        set_num(page, "FloorPctHeated", 30)
        roof_sum = (
            float(page.input_value("#RoofPctExt"))
            + float(page.input_value("#RoofPctUnh"))
            + float(page.input_value("#RoofPctHeated"))
        )
        floor_sum = (
            float(page.input_value("#FloorPctGround"))
            + float(page.input_value("#FloorPctUnh"))
            + float(page.input_value("#FloorPctHeated"))
        )

        # Now set a valid distribution that should reduce external losses/gains.
        set_num(page, "RoofPctExt", 50)
        set_num(page, "RoofPctUnh", 0)
        set_num(page, "RoofPctHeated", 50)
        set_num(page, "FloorPctGround", 50)
        set_num(page, "FloorPctUnh", 0)
        set_num(page, "FloorPctHeated", 50)
        calc(page)
        s1 = parse_total_w(page)
        changed = abs(s1 - s0)
        ok8 = (roof_sum <= 100.01) and (floor_sum <= 100.01) and (changed > 0.5)
        results.append(("heated_pct_sensitivity", ok8, {
            "before": s0,
            "after": s1,
            "abs_diff": changed,
            "roof_sum_after_invalid_input": roof_sum,
            "floor_sum_after_invalid_input": floor_sum
        }))
        if not ok8:
            bugs.append("Envelope percentage controls failed: sum capping or sensitivity behavior is wrong")

        # 9) Fuzz: random valid inputs should not crash and produce finite total
        fuzz_fail = 0
        for _ in range(60):
            L = round(random.uniform(2.5, 20), 2)
            W = round(random.uniform(2.0, 15), 2)
            H = round(random.uniform(2.2, 4.5), 2)
            Tin = round(random.uniform(20, 27), 1)
            Tout = round(random.uniform(30, 43), 1)
            RHin = round(random.uniform(30, 65), 0)
            RHout = round(random.uniform(20, 80), 0)
            set_num(page, "L", L)
            set_num(page, "W", W)
            set_num(page, "H", H)
            set_num(page, "Tin", Tin)
            set_num(page, "Tout", Tout)
            set_num(page, "RHin", RHin)
            set_num(page, "RHout", RHout)
            calc(page)
            try:
                t = parse_total_w(page)
                if not (t >= 0 and t < 1e8):
                    fuzz_fail += 1
            except Exception:
                fuzz_fail += 1
        ok9 = fuzz_fail == 0
        results.append(("fuzz_60_cases", ok9, {"failed_cases": fuzz_fail}))
        if not ok9:
            bugs.append(f"Fuzz testing found {fuzz_fail} unstable/invalid output cases")

        browser.close()

    report = {
        "pass": sum(1 for _, ok, _ in results if ok),
        "total": len(results),
        "results": [
            {"test": name, "ok": ok, "details": details}
            for name, ok, details in results
        ],
        "bugs": bugs,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()



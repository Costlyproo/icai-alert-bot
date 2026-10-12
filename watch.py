#!/usr/bin/env python3
"""
ICAI batch watcher: Mumbai city (no Navi Mumbai), Vasai, Sikar.

Watches the PUBLIC ICAI batch calendar
  https://www.icaionlineregistration.org/LaunchBatchDetail.aspx
for the VASAI centre (Western region) and two courses:
  * AICITSS - Advanced Information Technology   (batches AICITSSAdvITT__VASAI_NN)
  * Advanced (ICITSS) MCS Course  (non-weekend) (batches AdvICITSSMCS__VASAI_NN)

It only does what a browser does: load the page, pick Region -> Pou -> Course
(ASP.NET postbacks) and press "Get List". One run = a handful of requests.

Alerts go to Telegram (secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). With no
secrets set, messages are printed to stdout instead (handy for local testing).
"""
import base64
import csv
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------- settings
URL = "https://www.icaionlineregistration.org/LaunchBatchDetail.aspx"
REG_LINK = "https://www.icaionlineregistration.org/"
REGION_NAME = "Western"          # dropdown text
REGION_FALLBACK_ID = "2"
POU_NAME = "VASAI"
POU_FALLBACK_ID = "85"
# key -> (label, function deciding if a course option text matches, fallback id)
COURSES = {
    "ADVITT": ("Adv ITT (AICITSS - Advanced IT)",
               lambda t: "advanced information technology" in t.lower(), "48"),
    "MCS": ("MCS (Advanced ICITSS MCS, non-weekend)",
            lambda t: "mcs" in t.lower() and "weekend" not in t.lower(), "45"),
}
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 ICAIBatchWatcher/1.0 (personal seat alert, 1 run per 5 min)")
FAIL_WARN_AFTER = 6              # consecutive failed runs before one warning
BURST_COUNT = 4
BURST_GAP_S = 3

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("STATE_DIR", HERE)
STATE_FILE = os.path.join(STATE_DIR, "state.json")
HISTORY_FILE = os.path.join(STATE_DIR, "history.csv")
HEARTBEAT_FILE = os.path.join(STATE_DIR, "heartbeat.txt")

IST = timezone(timedelta(hours=5, minutes=30))
JST = timezone(timedelta(hours=9))

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CALLMEBOT_USER = os.environ.get("CALLMEBOT_USER", "").strip()


def now_strs():
    n = datetime.now(timezone.utc)
    return (n.astimezone(IST).strftime("%d-%b-%Y %H:%M:%S IST"),
            n.astimezone(JST).strftime("%d-%b-%Y %H:%M:%S JST"))


def log(*a):
    print("[watch]", *a, flush=True)


# ----------------------------------------------------------------- fetching
def _hidden_fields(soup):
    return {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}


def _options(soup, select_id):
    return [(o.get("value", ""), o.get_text(strip=True)) for o in soup.select(f"#{select_id} option")]


def _request(session, method, data=None, attempts=3):
    delay = 10
    last = None
    for i in range(attempts):
        try:
            r = session.request(method, URL, data=data, timeout=45)
            if r.status_code == 200 and "__VIEWSTATE" in r.text:
                return r.text
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = repr(e)
        if i < attempts - 1:
            log(f"request failed ({last}); retrying in {delay}s")
            time.sleep(delay)
            delay *= 3
    raise RuntimeError(f"fetch failed after {attempts} attempts: {last}")


def fetch_all():
    """Return {key: {...batch...}} for every watched course at Vasai."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})
    html = _request(s, "GET")
    soup = BeautifulSoup(html, "html.parser")

    region_id = next((v for v, t in _options(soup, "ddl_reg") if t.lower() == REGION_NAME.lower()),
                     REGION_FALLBACK_ID)
    course_opts = _options(soup, "ddl_course")
    course_ids = {}
    for key, (_, match, fb) in COURSES.items():
        course_ids[key] = next((v for v, t in course_opts if match(t)), fb)

    # select region (autopostback fills the Pou list)
    first_course = next(iter(course_ids.values()))
    d = _hidden_fields(soup)
    d.update({"__EVENTTARGET": "ddl_reg", "__EVENTARGUMENT": "", "ddl_reg": region_id,
              "ddl_course": first_course})
    time.sleep(1)
    html = _request(s, "POST", d)
    soup = BeautifulSoup(html, "html.parser")
    pou_id = next((v for v, t in _options(soup, "ddlPou") if t.strip().upper() == POU_NAME),
                  POU_FALLBACK_ID)
    base = _hidden_fields(soup)

    out = {}
    for key, cid in course_ids.items():
        d = dict(base)
        d.update({"__EVENTTARGET": "", "__EVENTARGUMENT": "", "ddl_reg": region_id,
                  "ddlPou": pou_id, "ddl_course": cid, "btn_getlist": "Get List"})
        time.sleep(1.5)
        html = _request(s, "POST", d)
        rows = parse_batches(html)
        for r in rows:
            r["course_key"] = key
            r["course_label"] = COURSES[key][0]
            r["possibly_weekend"] = key == "MCS" and looks_weekend(r)
            out[r["batch"]] = r
        log(f"{key}: course id {cid}, pou id {pou_id}, {len(rows)} batches")
    return out


# ----------------------------------------------------------------- parsing
BATCH_RE = re.compile(r"^[A-Za-z]+__[A-Za-z .]+_\d+$")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _viewstate_strings(html):
    """Extract the GridView's per-row Text values from __VIEWSTATE (it is plain,
    unencrypted data the page sends to every visitor)."""
    m = re.search(r'id="__VIEWSTATE" value="([^"]*)"', html)
    if not m:
        return {}
    try:
        b = base64.b64decode(m.group(1))
    except Exception:
        return {}
    # Strings appear as: 0x1f <idx of "Text"> 0x05 <len> <bytes>; the very first
    # one introduces the name instead: 0x1e 0x04 "Text" 0x05 <len> <bytes>
    rows, cur = {}, None
    for mm in re.finditer(rb"(?:\x1f[\x00-\x7f]|\x1e\x04Text)\x05([\x00-\x7f])", b):
        ln = mm.group(1)[0]
        st = b[mm.end():mm.end() + ln].decode("utf-8", "replace")
        if BATCH_RE.match(st):
            cur = st
            rows[cur] = []
        elif cur is not None:
            rows[cur].append(st)
    return rows


def parse_batches(html):
    soup = BeautifulSoup(html, "html.parser")
    vs = _viewstate_strings(html)
    out = []
    for tr in soup.select("#GridView1 tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        def span(name):
            el = tr.find("span", id=re.compile(name + r"_\d+$"))
            return el.get_text(strip=True) if el else ""

        batch = span("lblBatchHeaderId")
        if not batch:
            continue
        seats_txt = span("lblPublishSize")
        try:
            seats = int(seats_txt)
        except ValueError:
            seats = None
        r = {
            "batch": batch,
            "seats": seats,
            "from": span("lblFromDate"),
            "to": span("lblTodate"),
            "timing": span("lblBatchTiming"),
            "pou": span("lblCityName"),
            "course": span("lblCourseName"),
            "open_for": span("lblbatchStatus"),
            "registered": None,
            "reg_start": tds[8].get_text(" ", strip=True) if len(tds) > 8 else "",
            "status": "",
        }
        # hidden extras. Observed order after the 8 visible values:
        # course_id, registered_count, registration_start_date, status_text
        extra = vs.get(batch, [])
        tail = extra[7:] if len(extra) >= 8 else []
        if len(tail) >= 2 and tail[1].isdigit():
            r["registered"] = int(tail[1])
        dates = [x for x in tail[2:] if DATE_RE.match(x)]
        if dates:
            r["reg_start"] = dates[0]
        txt = [x for x in tail[2:] if not DATE_RE.match(x) and not x.isdigit()]
        if txt:
            r["status"] = txt[0]
        out.append(r)
    return out


def looks_weekend(r):
    text = (r["batch"] + " " + r["course"] + " " + r["timing"]).lower()
    if re.search(r"weekend|\b(sat|sun)(urday|day)?\b", text):
        return True
    try:
        f = datetime.strptime(r["from"], "%d/%m/%Y")
        t = datetime.strptime(r["to"], "%d/%m/%Y")
        if (t - f).days > 35:   # regular MCS runs ~2.5-3 weeks; weekend ones span months
            return True
    except ValueError:
        pass
    return False


# ----------------------------------------------------------------- messaging
def tg_send(text, silent=False):
    if not (TG_TOKEN and TG_CHAT):
        print(("\n----- TELEGRAM (silent) -----\n" if silent else "\n----- TELEGRAM -----\n") + text, flush=True)
        return True
    for i in range(3):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              data={"chat_id": TG_CHAT, "text": text,
                                    "disable_notification": "true" if silent else "false",
                                    "disable_web_page_preview": "true"}, timeout=30)
            if r.ok:
                return True
            log("telegram error", r.status_code, r.text[:200])
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)))
                continue
            if 400 <= r.status_code < 500:
                return False
        except requests.RequestException as e:
            log("telegram exception", e)
        time.sleep(3 * (i + 1))
    return False


def callmebot(text):
    if not CALLMEBOT_USER:
        return
    user = CALLMEBOT_USER if CALLMEBOT_USER.startswith(("@", "+")) else "@" + CALLMEBOT_USER
    url = ("https://api.callmebot.com/start.php?" +
           urllib.parse.urlencode({"user": user, "text": text[:250],
                                   "lang": "en-IN-Standard-A", "rpt": "2"}))
    if not (TG_TOKEN and TG_CHAT):
        print("\n----- CALLMEBOT CALL (not placed in local mode) -----\n" + url, flush=True)
        return
    try:
        r = requests.get(url, timeout=60)
        log("callmebot", r.status_code, re.sub(r"<[^>]+>", " ", r.text)[:200])
    except requests.RequestException as e:
        log("callmebot exception", e)


def fmt_batch(r):
    seats = "?" if r.get("seats") is None else r["seats"]
    lines = [
        f"Course: {r['course_label']}",
        f"Batch: {r['batch']}" + ("  (⚠️ possibly weekend)" if r.get("possibly_weekend") else ""),
        f"Dates: {r['from']} → {r['to']}",
        f"Timing: {r['timing']}",
        f"Seats left: {seats}" + (f"  (registered: {r['registered']})" if r.get("registered") is not None else ""),
    ]
    if r.get("reg_start"):
        lines.append(f"Reg start date: {r['reg_start']}")
    return "\n".join(lines)


# ----------------------------------------------------------------- centres
# (key, region name, region fallback id, Pou dropdown matcher)
CENTRES = [
    ("MUMBAI", "Western", "2", lambda t: "MUMBAI" in t.upper() and "NAVI" not in t.upper()),
    ("VASAI", "Western", "2", lambda t: t.strip().upper() == "VASAI"),
    ("SIKAR", "Central", "5", lambda t: t.strip().upper() == "SIKAR"),
]
BURST_COUNT = 3
BURST_GAP_S = 4


def fetch_centre(region_name, region_fb, pou_match):
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})
    soup = BeautifulSoup(_request(s, "GET", attempts=2), "html.parser")
    region_id = next((v for v, t in _options(soup, "ddl_reg") if t.lower() == region_name.lower()), region_fb)
    copts = _options(soup, "ddl_course")
    cids = {k: next((v for v, t in copts if m(t)), fb) for k, (_, m, fb) in COURSES.items()}
    d = _hidden_fields(soup)
    d.update({"__EVENTTARGET": "ddl_reg", "__EVENTARGUMENT": "", "ddl_reg": region_id,
              "ddl_course": next(iter(cids.values()))})
    time.sleep(1)
    soup = BeautifulSoup(_request(s, "POST", d, attempts=2), "html.parser")
    pous = [(v, t) for v, t in _options(soup, "ddlPou") if v and pou_match(t)]
    if not pous:
        raise RuntimeError("centre not found in dropdown")
    base = _hidden_fields(soup)
    out = {}
    for pid, pname in pous:
        for key, cid in cids.items():
            d = dict(base)
            d.update({"__EVENTTARGET": "", "__EVENTARGUMENT": "", "ddl_reg": region_id, "ddlPou": pid,
                      "ddl_course": cid, "btn_getlist": "Get List"})
            time.sleep(1.5)
            rows = parse_batches(_request(s, "POST", d, attempts=2))
            for r in rows:
                r["course_key"] = key
                r["course_label"] = COURSES[key][0]
                r["possibly_weekend"] = key == "MCS" and looks_weekend(r)
                r["centre_entry"] = pname
                out[r["batch"]] = r
            log(f"{pname} {key}: {len(rows)} batches")
    return out


def fetch_with_retry(*a):
    try:
        return fetch_centre(*a)
    except Exception as e:
        log("first attempt failed:", e)
        time.sleep(5)
        return fetch_centre(*a)


def starts_dec_or_later(r):
    try:
        return datetime.strptime(r["from"], "%d/%m/%Y") >= datetime(2026, 12, 1)
    except ValueError:
        return True


def urgent_burst(centre, events, test=False):
    ist, jst = now_strs()
    head = f"🚨 {centre} SEAT OPEN 🚨" + ("  [TEST ALERT – not real]" if test else "")
    body = "\n\n".join(f"{why}\n{fmt_batch(r)}" for why, r in events)
    short = ", ".join(f"{r['batch']} ({r['seats']} seats)" for _, r in events)
    tg_send(f"{head}\n\n{body}\n\nSeen: {ist} / {jst}\nRegister NOW: {REG_LINK}")
    for i in range(1, BURST_COUNT):
        time.sleep(BURST_GAP_S)
        tg_send(f"🚨 {centre} SEAT OPEN 🚨 ({i + 1}/{BURST_COUNT})" + (" [TEST]" if test else "")
                + f"\n{short}\n{REG_LINK}")
    names = " and ".join(r["batch"].split("__")[-1].replace("_", " ") for _, r in events[:3])
    kinds = " / ".join(sorted({"MCS" if r["course_key"] == "MCS" else "Advanced I T T" for _, r in events}))
    callmebot(("Test alert. " if test else "") +
              f"{centre.title()} {kinds} seat open. {names}. Register now on the I C A I portal.")


# ----------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"centres": {}, "fail_count": 0, "fail_warned": False}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")


def append_history(rows, ist):
    new = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["seen_ist", "centre", "course", "batch", "seats_left", "registered", "from", "to",
                        "timing", "reg_start", "status", "event"])
        for centre, ev, r in rows:
            w.writerow([ist, centre, r.get("course_key"), r["batch"], r.get("seats"), r.get("registered"),
                        r.get("from"), r.get("to"), r.get("timing"), r.get("reg_start", ""), r.get("status", ""), ev])


def heartbeat():
    """Daily commit so GitHub doesn't disable the cron after 60 days."""
    today = datetime.now(IST).strftime("%Y-%m-%d")
    old = open(HEARTBEAT_FILE).read().strip() if os.path.exists(HEARTBEAT_FILE) else ""
    if not old.startswith(today):
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(f"{today} last daily heartbeat ({now_strs()[0]})\n")


# ----------------------------------------------------------------- main
def run_test_alert():
    sample = {"batch": "AICITSSAdvITT__MUMBAI_180", "seats": 45, "registered": 0,
              "from": "28/12/2026", "to": "12/01/2027", "timing": "8-0-AM to 2-0-PM",
              "course_label": COURSES["ADVITT"][0], "course_key": "ADVITT",
              "reg_start": "13/10/2026", "possibly_weekend": False}
    urgent_burst("MUMBAI", [("SAMPLE: new batch listed", sample)], test=True)
    tg_send("✅ Test finished. The GitHub bot is set up and watches Mumbai city, Vasai and Sikar every ~5 min. "
            "(This quiet message should arrive without sound.)", silent=True)


def check_centre(key, cur, old, ist, jst, hist):
    """Return (urgent_events, normal_lines) according to the alert rules."""
    urgent, normal = [], []
    for name, r in cur.items():
        o = old.get(name)
        if r.get("possibly_weekend"):
            r["first_seen_ist"] = (o or {}).get("first_seen_ist", ist)
            continue
        seats = r.get("seats")
        if o is None:
            r["first_seen_ist"] = ist
            hist.append((key, "new", r))
            if key == "MUMBAI" or (seats and seats > 0):
                urgent.append(("🆕 NEW batch listed", r))
            else:
                normal.append(f"🆕 New batch listed but already FULL\n{fmt_batch(r)}")
            continue
        r["first_seen_ist"] = o.get("first_seen_ist", ist)
        os_ = o.get("seats")
        if seats == os_:
            continue
        reopen = (os_ in (0, None)) and bool(seats and seats > 0)
        hist.append((key, "reopened" if reopen else "seats_change", r))
        if key == "MUMBAI":
            if reopen:
                urgent.append((f"🔓 Seats REOPENED (was {os_}, now {seats})", r))
            continue
        if r["course_key"] == "ADVITT" and reopen:
            urgent.append((f"🔓 Seats REOPENED (was {os_}, now {seats})", r))
        elif starts_dec_or_later(r):
            if reopen:
                urgent.append((f"🔓 Seats REOPENED (was {os_}, now {seats})", r))
            else:
                normal.append(f"↕️ {name} ({r['from']}–{r['to']}): seats {os_} → {seats}")
    return urgent, normal


def main():
    if os.environ.get("TEST_ALERT", "").strip().lower() in ("1", "true", "yes"):
        run_test_alert()
        return 0

    st = load_state()
    st.setdefault("centres", {})
    ist, jst = now_strs()
    errors, hist = [], []
    for key, rname, rfb, pm in CENTRES:
        cs = st["centres"].setdefault(key, {"initialized": False, "batches": {}})
        try:
            cur = fetch_with_retry(rname, rfb, pm)
        except Exception as e:
            errors.append(f"{key}: {e}")
            log("FETCH FAILED", key, e)
            continue
        old = cs.get("batches", {})
        if not cs.get("initialized") or not old:
            for r in cur.values():
                r["first_seen_ist"] = ist
            openb = [r for r in cur.values() if (r.get("seats") or 0) > 0 and not r.get("possibly_weekend")]
            msg = f"ℹ️ {key} watch started ({len(cur)} batches)."
            msg += ("\nBatches with seats now:\n" + "\n".join(
                f"• {r['batch']}: {r['seats']} seats, {r['from']}–{r['to']}" for r in openb)) if openb \
                else "\nNo batches have seats right now."
            tg_send(msg, silent=True)
            cs["initialized"] = True
        else:
            urgent, normal = check_centre(key, cur, old, ist, jst, hist)
            if urgent:
                urgent_burst(key, urgent)
            if normal:
                tg_send(f"{key} batch update\n" + "\n\n".join(normal) + f"\nSeen: {ist} / {jst}\n{REG_LINK}")
        cs["batches"] = cur
        cs["last_check_ist"] = ist

    if errors and len(errors) == len(CENTRES):
        st["fail_count"] = st.get("fail_count", 0) + 1
        st["last_error"] = f"{ist}: {'; '.join(errors)}"
        if st["fail_count"] >= FAIL_WARN_AFTER and not st.get("fail_warned"):
            tg_send(f"⚠️ ICAI watcher: couldn't read the ICAI batch page for {st['fail_count']} runs in a row "
                    f"(~{st['fail_count'] * 5} min).\nLast error: {errors[0]}\nI'll keep trying. "
                    f"Meanwhile check manually: {URL}")
            st["fail_warned"] = True
    else:
        if st.get("fail_warned"):
            tg_send(f"✅ ICAI watcher recovered at {ist}; the page is readable again.", silent=True)
        st["fail_count"] = 0
        st["fail_warned"] = False
        st["last_error"] = "; ".join(errors) or None
    if hist:
        append_history(hist, ist)
    save_state(st)
    heartbeat()
    log(f"done; errors: {errors or 'none'}; events: {len(hist)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

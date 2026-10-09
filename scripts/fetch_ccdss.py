"""fetch_ccdss.py -- download CCDSS series from the PHAC Public Health Infobase data tool.

PURPOSE
  The data tool has no bulk download; each condition, geography and measure is one page. This script asks for each
  one in turn and appends the rows the page carries (every year, both sexes, female, male) to a JSONL file.

CONFIG
  argv[1]   output JSONL (appended)
  argv[2]   condition ids, comma separated (1 diabetes, 2 hypertension, 7 COPD, 6 asthma, 16 dementia)
  argv[3]   geography codes (00 Canada, 10-62 provinces and territories)
  argv[4]   measures (1 age-standardized incidence, 2 age-standardized prevalence)
  example:  python scripts/fetch_ccdss.py data/raw/ccdss-datatool.jsonl 1,2,7,6,16 00,10,11,12,13,24,35,46,47,48,59,60,61,62 1,2

WORKFLOW
  1. open the tool, then for each selection post the form as the page's own dropdowns do
  2. read the selection the page echoes back and store it beside every row (prepare_data.py refuses a mismatch)
  3. an empty answer is retried once by stepping to another measure and back

Q&A
  Q: is this allowed?   A: robots.txt (read 2026-10-08) disallows only indicator-details and description pages;
     the data tool is allowed. Requests are 3 seconds apart. Data: Open Government Licence - Canada.

PITFALLS
  * conditions 4 (mood and anxiety care use) and 39 (multimorbidity) answered HTTP 500 on 2026-10-08.
  * the tool is an ASP.NET page; if PHAC redesigns it, the form field names here stop matching.

NOTES
  Written 2026-10-08; one run, 140 series, 9,912 rows.
"""
import html, http.cookiejar, json, re, sys, time, urllib.parse, urllib.request, os

URL = "https://health-infobase.canada.ca/ccdss/data-tool/"
UA = {"User-Agent": "Mozilla/5.0 portfolio-research (PHAC open data, polite)"}
OUT = sys.argv[1]
VARS = [v for v in sys.argv[2].split(",")]
GEOS = sys.argv[3].split(",")
MEAS = sys.argv[4].split(",")

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(data=None):
    req = urllib.request.Request(URL, data=data, headers=UA)
    return op.open(req, timeout=90).read().decode("utf-8", "replace")


def hidden(t):
    return {n: html.unescape(v) for n, v in re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', t)}


def selected(t, name):
    m = re.search(r'<select[^>]*id="' + name + r'".*?</select>', t, re.S)
    s = re.search(r'<option selected="selected" value="([^"]*)"', m.group(0))
    return s.group(1) if s else None


def post(t, target, fields):
    form = hidden(t)
    base = {
        "ctl00$ContentPlaceHolder_Body$ddlGeo": selected(t, "ddlGeo"),
        "ctl00$ContentPlaceHolder_Body$ddlVar": selected(t, "ddlVar"),
        "ctl00$ContentPlaceHolder_Body$ddlMS": selected(t, "ddlMS"),
        "ctl00$ContentPlaceHolder_Body$RBSex": "B",
    }
    base.update(fields)
    form.update(base)
    form["__EVENTTARGET"] = target
    form["__EVENTARGUMENT"] = ""
    time.sleep(3)
    return get(urllib.parse.urlencode(form).encode())


t = get()
n = 0
with open(OUT, "a", encoding="utf-8", newline="\n") as f:
    for v in VARS:
        t = post(t, "ctl00$ContentPlaceHolder_Body$ddlVar", {"ctl00$ContentPlaceHolder_Body$ddlVar": v})
        for g in GEOS:
            t = post(t, "ctl00$ContentPlaceHolder_Body$ddlGeo", {"ctl00$ContentPlaceHolder_Body$ddlVar": v,
                                                               "ctl00$ContentPlaceHolder_Body$ddlGeo": g})
            for ms in MEAS:
                t = post(t, "ctl00$ContentPlaceHolder_Body$ddlMS", {"ctl00$ContentPlaceHolder_Body$ddlVar": v,
                                                                  "ctl00$ContentPlaceHolder_Body$ddlGeo": g,
                                                                  "ctl00$ContentPlaceHolder_Body$ddlMS": ms})
                got = (selected(t, "ddlVar"), selected(t, "ddlGeo"), selected(t, "ddlMS"))
                m = re.search(r"var data = (\[.*?\]);", t, re.S)
                rows = json.loads(m.group(1)) if m else []
                if not rows:  # an unchanged selection can return an empty panel: step away and back
                    other = "4" if ms != "4" else "5"
                    for x in (other, ms):
                        t = post(t, "ctl00$ContentPlaceHolder_Body$ddlMS", {"ctl00$ContentPlaceHolder_Body$ddlVar": v,
                                                                          "ctl00$ContentPlaceHolder_Body$ddlGeo": g,
                                                                          "ctl00$ContentPlaceHolder_Body$ddlMS": x})
                    got = (selected(t, "ddlVar"), selected(t, "ddlGeo"), selected(t, "ddlMS"))
                    m = re.search(r"var data = (\[.*?\]);", t, re.S)
                    rows = json.loads(m.group(1)) if m else []
                    print("  retried", got, len(rows), flush=True)
                for r in rows:
                    r.update({"var_id": v, "geo_id": g, "ms_id": ms, "echo": "|".join(str(x) for x in got)})
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                n += 1
                print(v, g, ms, "echo", got, "rows", len(rows), flush=True)
print("requests", n)

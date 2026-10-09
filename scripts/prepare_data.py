"""prepare_data.py -- turn the CCDSS data-tool download into the three tables the Power BI model loads.

PURPOSE
  The download (scripts/fetch_ccdss.py) is one JSON row per year and sex for each condition, geography and measure.
  The report wants a fact table and two small dimensions, as CSV, so the model is plain Power Query over files.

CONFIG
  argv[1]   the downloaded JSONL (data/raw/ccdss-datatool.jsonl)
  argv[2]   the data folder; it must hold ccdss_case_definitions_v2024.xlsx (PHAC, Open Government Licence)

WORKFLOW
  1. refuse the run if any row came back for a selection other than the one requested (the echo check)
  2. keep one row per condition, geography, measure, year and sex
  3. write rates.csv, geography.csv, and condition.csv with the case definition from the PHAC workbook
  4. print the counts: series requested and present, rows with no rate, flags seen

Q&A
  Q: why are some rates blank?   A: the data tool leaves a year empty where it has no data for that province and
     marks it with a flag; the blank is kept, never filled.

PITFALLS
  * rates are age-standardized; the case counts beside them are not, they are people.
  * the condition names here are short labels; the full specification is in condition.csv.

NOTES
  Written 2026-10-08. Data: Public Health Agency of Canada, Canadian Chronic Disease Surveillance System,
  Public Health Infobase data tool, read 2026-10-08.
"""
import csv
import io
import json
import os
import sys

import openpyxl

RAW, OUT = sys.argv[1], sys.argv[2]

GEO = [  # code, name, short, level, sort
    ("00", "Canada", "CAN", "Canada", 0),
    ("10", "Newfoundland and Labrador", "NL", "Province/territory", 1),
    ("11", "Prince Edward Island", "PE", "Province/territory", 2),
    ("12", "Nova Scotia", "NS", "Province/territory", 3),
    ("13", "New Brunswick", "NB", "Province/territory", 4),
    ("24", "Quebec", "QC", "Province/territory", 5),
    ("35", "Ontario", "ON", "Province/territory", 6),
    ("46", "Manitoba", "MB", "Province/territory", 7),
    ("47", "Saskatchewan", "SK", "Province/territory", 8),
    ("48", "Alberta", "AB", "Province/territory", 9),
    ("59", "British Columbia", "BC", "Province/territory", 10),
    ("60", "Yukon", "YT", "Province/territory", 11),
    ("61", "Northwest Territories", "NT", "Province/territory", 12),
    ("62", "Nunavut", "NU", "Province/territory", 13),
]
# data-tool variable id -> short name, sort, row in the case-definition workbook.
# Mood and anxiety (4) and multimorbidity (39) were requested too; the data tool answered HTTP 500 on 2026-10-08.
COND = {
    "1": ("Diabetes", 1, 6),
    "2": ("Hypertension", 2, 14),
    "7": ("COPD", 3, 13),
    "6": ("Asthma", 4, 11),
    "16": ("Dementia", 5, 20),
}
MEASURE = {"1": "Incidence", "2": "Prevalence"}

rows = [json.loads(l) for l in io.open(RAW, encoding="utf-8")]
bad_echo = [r for r in rows if r["echo"] != "|".join((r["var_id"], r["geo_id"], r["ms_id"]))]
assert not bad_echo, "%d rows came back for a selection other than the one requested" % len(bad_echo)

facts, seen = [], set()
for r in rows:
    key = (r["var_id"], r["geo_id"], r["ms_id"], r["S_Year"], r["SexID"])
    if key in seen:
        continue
    seen.add(key)
    facts.append({
        "ConditionId": int(r["var_id"]),
        "GeoCode": r["geo_id"],
        "MeasureType": MEASURE[r["ms_id"]],
        "Year": r["S_Year"],
        "FiscalYear": r["FY_yr"].replace("–", "-"),
        "Sex": r["SexName"],
        "AgeGroup": r["AgeGrpName"].replace("–", "-"),
        "Rate": "" if r["Rate"] is None else r["Rate"],
        "CILow": "" if r["CI95L"] is None else r["CI95L"],
        "CIHigh": "" if r["CI95H"] is None else r["CI95H"],
        "Cases": "" if r["R_Num"] is None else int(r["R_Num"]),
        "Unit": "%" if r["VarUnitID"] == "PERCENT" else "per 100,000",
        "Flag": r["flag_str"].strip(),
    })

os.makedirs(OUT, exist_ok=True)
with io.open(os.path.join(OUT, "rates.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(facts[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(sorted(facts, key=lambda x: (x["ConditionId"], x["GeoCode"], x["MeasureType"], x["Sex"], x["Year"])))

with io.open(os.path.join(OUT, "geography.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["GeoCode", "Geography", "GeoShort", "Level", "GeoSort"])
    w.writerows(GEO)

ws = openpyxl.load_workbook(os.path.join(OUT, "ccdss_case_definitions_v2024.xlsx"), read_only=True).worksheets[0]
grid = list(ws.iter_rows(values_only=True))


def clean(s):
    return " ".join(str(s or "").split()).replace("–", "-")


with io.open(os.path.join(OUT, "condition.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["ConditionId", "Condition", "ConditionSort", "Specification", "CaseDefinition", "Hospital",
                "PhysicianClaims", "Prevalence", "Incidence", "Ages"])
    for vid, (short, sort, row) in COND.items():
        g = grid[row - 1]
        w.writerow([int(vid), short, sort, clean(g[1]), clean(g[2]), clean(g[3]), clean(g[4]), clean(g[6]),
                    clean(g[7]), clean(g[27])])

print("raw rows", len(rows), "| fact rows", len(facts), "| duplicates dropped", len(rows) - len(facts))
have = {(f["ConditionId"], f["GeoCode"], f["MeasureType"]) for f in facts}
print("series", len(have), "of", len(COND) * len(GEO) * 2, "requested")
missing = sorted({(int(v), g, m) for v in COND for g, *_ in GEO for m in MEASURE.values()} - have)
print("series with no rows:", missing if missing else "none")
blank = sum(1 for f in facts if f["Rate"] == "")
print("rows with no rate (flagged or not reported):", blank, "| flags:",
      sorted({f["Flag"] for f in facts if f["Flag"]}))

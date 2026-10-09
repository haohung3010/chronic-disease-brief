"""build_pbip.py -- write the Power BI project (PBIP: TMDL semantic model + PBIR report) from this file.

PURPOSE
  The report is source code: every table, relationship, DAX measure, page and visual is declared here, so a reviewer
  reads the whole build in one file and a change is a diff. Power BI Desktop opens the .pbip it writes.

CONFIG
  argv[1]   the repository root (the .pbip and its two folders are written to <root>/report/)
  argv[2]   the absolute data folder the model reads CSVs from (stored as the DataFolder parameter)

WORKFLOW
  1. semantic model: three CSV tables, two relationships, the measures below (TMDL)
  2. report: three pages, each visual a JSON file (PBIR), theme in StaticResources
  3. print what was written

Q&A
  Q: why not save a .pbix only?   A: a .pbix is a zip nobody can review; the PBIP is the source and the .pbix is
     saved from it in Power BI Desktop as the downloadable copy.
  Q: the DataFolder path?   A: change it in Power BI Desktop (Transform data > Manage parameters) after cloning.

PITFALLS
  * rates are age-standardized; every rate measure fixes Sex to the selected value, or Both sexes.
  * "latest year" is computed per condition and geography, so a province missing a year shows its own latest.

NOTES
  Written 2026-10-08 for Power BI Desktop 2.157 (August 2026).
"""
import io
import json
import os
import shutil
import sys

ROOT, DATA = sys.argv[1], sys.argv[2]
NAME = "Chronic Disease Brief"
OUT = os.path.join(ROOT, "report")
SM = os.path.join(OUT, NAME + ".SemanticModel")
RP = os.path.join(OUT, NAME + ".Report")

# ---------------------------------------------------------------- palette (also in the theme)
INK, SLATE, MUTED, RULE, PAPER = "#1C2A33", "#4A5B66", "#7C8B94", "#D9E0E4", "#FFFFFF"
TEAL, TEAL_LIGHT, AMBER, CANADA_GREY = "#0F6E6E", "#8CC3BF", "#D9822B", "#9AA7AF"


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def wj(path, obj):
    w(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# ================================================================ SEMANTIC MODEL (TMDL)
def csv_partition(table, file, types):
    t = ", ".join('{"%s", %s}' % (c, ty) for c, ty in types)
    return ("\tpartition %s = m\n\t\tmode: import\n\t\tsource =\n"
            "\t\t\t\tlet\n"
            "\t\t\t\t    Source = Csv.Document(File.Contents(DataFolder & \"\\%s\"), [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
            "\t\t\t\t    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n"
            "\t\t\t\t    Typed = Table.TransformColumnTypes(Promoted, {%s}, \"en-US\")\n"
            "\t\t\t\tin\n"
            "\t\t\t\t    Typed\n\n" % (table, file, t))


def column(name, dtype, fmt=None, summarize="none", sort_by=None, hidden=False):
    s = "\tcolumn %s\n\t\tdataType: %s\n" % (q(name), dtype)
    if fmt:
        s += "\t\tformatString: %s\n" % fmt
    if hidden:
        s += "\t\tisHidden\n"
    s += "\t\tsummarizeBy: %s\n\t\tsourceColumn: %s\n" % (summarize, name)
    if sort_by:
        s += "\t\tsortByColumn: %s\n" % q(sort_by)
    return s + "\n"


def q(n):
    return "'%s'" % n if any(c in n for c in " -+,()/") else n


def measure(name, dax, fmt=None, folder=None, desc=None):
    lines = dax.strip("\n").split("\n")
    s = ""
    if desc:
        s += "\t/// %s\n" % desc
    if len(lines) == 1:
        s += "\tmeasure %s = %s\n" % (q(name), lines[0].strip())
    else:
        s += "\tmeasure %s =\n" % q(name) + "".join("\t\t\t%s\n" % l for l in lines)
    if fmt:
        s += "\t\tformatString: %s\n" % fmt
    if folder:
        s += "\t\tdisplayFolder: %s\n" % folder
    return s + "\n"


MEASURES = [
    ("Selected sex", 'SELECTEDVALUE(Rates[Sex], "Both sexes")', None, "Helpers",
     "The sex the rate measures use: the slicer's choice, or Both sexes."),
    ("Latest year", 'CALCULATE(MAX(Rates[Year]), REMOVEFILTERS(Rates[Year]), NOT ISBLANK(Rates[Rate]))', "0",
     "Helpers", "Most recent year with a reported rate, for the current condition and geography."),
    ("Base year", '[Latest year] - 10', "0", "Helpers", "Ten years before the latest year."),
    ("Prevalence (ASR %)",
     'VAR s = [Selected sex]\nRETURN CALCULATE(AVERAGE(Rates[Rate]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s)',
     "0.0", "Rates", "Age-standardized prevalence, percent."),
    ("Incidence (ASR per 100k)",
     'VAR s = [Selected sex]\nRETURN CALCULATE(AVERAGE(Rates[Rate]), Rates[MeasureType] = "Incidence", Rates[Sex] = s)',
     "#,0", "Rates", "Age-standardized rate of newly identified cases per 100,000."),
    ("Prevalence, latest",
     'VAR y = [Latest year]\nRETURN CALCULATE([Prevalence (ASR %)], Rates[Year] = y)', "0.0", "Rates", None),
    ("Prevalence, Canada",
     'CALCULATE([Prevalence (ASR %)], REMOVEFILTERS(Geography), Geography[GeoCode] = "00")', "0.0", "Rates",
     "The same rate for Canada, whatever geography is selected."),
    ("Prevalence, Canada latest",
     'VAR y = CALCULATE([Latest year], REMOVEFILTERS(Geography), Geography[GeoCode] = "00")\n'
     'RETURN CALCULATE([Prevalence (ASR %)], REMOVEFILTERS(Geography), Geography[GeoCode] = "00", Rates[Year] = y)',
     "0.0", "Rates", None),
    ("Gap to Canada (pts)",
     'VAR a = [Prevalence, latest]\nVAR c = [Prevalence, Canada latest]\nRETURN IF(NOT ISBLANK(a) && NOT ISBLANK(c), a - c)', "+0.0;-0.0;0.0", "Rates",
     "Percentage points above (+) or below (-) Canada, latest year."),
    ("Prevalence change, 10 yrs (pts)",
     'VAR y = [Latest year]\nVAR b = y - 10\n'
     'RETURN CALCULATE([Prevalence (ASR %)], Rates[Year] = y) - CALCULATE([Prevalence (ASR %)], Rates[Year] = b)',
     "+0.0;-0.0;0.0", "Rates", None),
    ("Incidence, latest",
     'VAR y = [Latest year]\nRETURN CALCULATE([Incidence (ASR per 100k)], Rates[Year] = y)', "#,0", "Rates", None),
    ("Incidence change, 10 yrs (%)",
     'VAR y = [Latest year]\nVAR b = y - 10\n'
     'VAR now_ = CALCULATE([Incidence (ASR per 100k)], Rates[Year] = y)\n'
     'VAR then_ = CALCULATE([Incidence (ASR per 100k)], Rates[Year] = b)\n'
     'RETURN DIVIDE(now_ - then_, then_)', "+0%;-0%;0%", "Rates",
     "Change in the age-standardized rate of new cases over ten years."),
    ("People living with it",
     'VAR s = [Selected sex]\nVAR y = [Latest year]\n'
     'RETURN CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s, Rates[Year] = y)',
     "#,0", "People", "Prevalent cases in the latest year: people, not a rate."),
    ("People added in 10 yrs",
     'VAR s = [Selected sex]\nVAR y = [Latest year]\nVAR b = y - 10\n'
     'VAR now_ = CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s, Rates[Year] = y)\n'
     'VAR then_ = CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s, Rates[Year] = b)\n'
     'RETURN now_ - then_', "+#,0;-#,0;0", "People",
     "Growth in prevalent cases over ten years: ageing and population growth included, unlike the rate."),
    ("Growth in people, 10 yrs (%)",
     'VAR s = [Selected sex]\nVAR y = [Latest year]\nVAR b = y - 10\n'
     'VAR now_ = CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s, Rates[Year] = y)\n'
     'VAR then_ = CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s, Rates[Year] = b)\n'
     'RETURN DIVIDE(now_ - then_, then_)', "+0%;-0%;0%", "People", None),
    ("New cases, latest",
     'VAR s = [Selected sex]\nVAR y = [Latest year]\n'
     'RETURN CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Incidence", Rates[Sex] = s, Rates[Year] = y)',
     "#,0", "People", "Newly identified cases in the latest year."),
    ("People living with it, by year",
     'VAR s = [Selected sex]\nRETURN CALCULATE(SUM(Rates[Cases]), Rates[MeasureType] = "Prevalence", Rates[Sex] = s)',
     "#,0", "People", "Prevalent cases in each year."),
    ("Province rank",
     'VAR cur = [Prevalence, latest]\n'
     'RETURN IF(NOT ISBLANK(cur), RANKX(FILTER(ALL(Geography), Geography[Level] = "Province/territory"), '
     '[Prevalence, latest], cur, DESC, Dense))',
     "0", "Rates", "Rank among the 13 provinces and territories by latest age-standardized prevalence; 1 = highest."),
    ("Bar colour",
     'IF(SELECTEDVALUE(Geography[GeoShort]) = "BC", "%s", "%s")' % (AMBER, TEAL_LIGHT), None, "Helpers",
     "Highlights British Columbia in the province comparison."),
]


def build_model():
    if os.path.isdir(SM):
        shutil.rmtree(SM)
    wj(os.path.join(SM, "definition.pbism"), {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}})
    d = os.path.join(SM, "definition")
    w(os.path.join(d, "database.tmdl"), "database\n\tcompatibilityLevel: 1600\n\n")
    w(os.path.join(d, "model.tmdl"),
      "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
      "\tdiscourageImplicitMeasures\n\tsourceQueryCulture: en-US\n\n"
      "ref table Rates\nref table Geography\nref table Condition\n\n"
      "ref cultureInfo en-US\n\n")
    w(os.path.join(d, "cultures", "en-US.tmdl"), "cultureInfo en-US\n\n")
    w(os.path.join(d, "expressions.tmdl"),
      'expression DataFolder = "%s" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n\n'
      % DATA.replace("/", chr(92)))
    w(os.path.join(d, "relationships.tmdl"),
      "relationship rates_geography\n\tfromColumn: Rates.GeoCode\n\ttoColumn: Geography.GeoCode\n\n"
      "relationship rates_condition\n\tfromColumn: Rates.ConditionId\n\ttoColumn: Condition.ConditionId\n\n")

    t = "table Rates\n\n"
    for name, dax, fmt, folder, desc in MEASURES:
        t += measure(name, dax, fmt, folder, desc)
    for c, ty, fmt, hid in [("ConditionId", "int64", "0", True), ("GeoCode", "string", None, True),
                            ("MeasureType", "string", None, False), ("Year", "int64", "0", False),
                            ("FiscalYear", "string", None, False), ("Sex", "string", None, False),
                            ("AgeGroup", "string", None, False), ("Rate", "double", "0.00", False),
                            ("CILow", "double", "0.00", False), ("CIHigh", "double", "0.00", False),
                            ("Cases", "int64", "#,0", False), ("Unit", "string", None, False),
                            ("Flag", "string", None, False)]:
        t += column(c, ty, fmt, hidden=hid)
    t += csv_partition("Rates", "rates.csv", [
        ("ConditionId", "Int64.Type"), ("GeoCode", "type text"), ("MeasureType", "type text"),
        ("Year", "Int64.Type"), ("FiscalYear", "type text"), ("Sex", "type text"), ("AgeGroup", "type text"),
        ("Rate", "type number"), ("CILow", "type number"), ("CIHigh", "type number"), ("Cases", "Int64.Type"),
        ("Unit", "type text"), ("Flag", "type text")])
    w(os.path.join(d, "tables", "Rates.tmdl"), t)

    t = "table Geography\n\n"
    t += column("GeoCode", "string", hidden=True)
    t += column("Geography", "string", sort_by="GeoSort")
    t += column("GeoShort", "string", sort_by="GeoSort")
    t += column("Level", "string")
    t += column("GeoSort", "int64", "0", hidden=True)
    t += csv_partition("Geography", "geography.csv", [("GeoCode", "type text"), ("Geography", "type text"),
                                                       ("GeoShort", "type text"), ("Level", "type text"),
                                                       ("GeoSort", "Int64.Type")])
    w(os.path.join(d, "tables", "Geography.tmdl"), t)

    t = "table Condition\n\n"
    t += column("ConditionId", "int64", "0", hidden=True)
    t += column("Condition", "string", sort_by="ConditionSort")
    t += column("ConditionSort", "int64", "0", hidden=True)
    for c in ["Specification", "CaseDefinition", "Hospital", "PhysicianClaims", "Prevalence", "Incidence", "Ages"]:
        t += column(c, "string")
    t += csv_partition("Condition", "condition.csv", [("ConditionId", "Int64.Type"), ("Condition", "type text"),
                                                       ("ConditionSort", "Int64.Type")] +
                       [(c, "type text") for c in ["Specification", "CaseDefinition", "Hospital", "PhysicianClaims",
                                                   "Prevalence", "Incidence", "Ages"]])
    w(os.path.join(d, "tables", "Condition.tmdl"), t)


# ================================================================ REPORT (PBIR)
def lit(v):
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": "%sD" % v}}}
    return {"expr": {"Literal": {"Value": "'%s'" % str(v).replace("'", "''")}}}


def colour(hexv):
    return {"solid": {"color": lit(hexv)}}


def col(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def mea(prop, entity="Rates"):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(field, display=None):
    k = "Column" if "Column" in field else "Measure"
    e, p = field[k]["Expression"]["SourceRef"]["Entity"], field[k]["Property"]
    o = {"field": field, "queryRef": "%s.%s" % (e, p), "nativeQueryRef": p}
    if display:
        o["displayName"] = display
    return o


def in_filter(entity, prop, values):
    return {"Version": 2, "From": [{"Name": "x", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "x"}},
                                                                        "Property": prop}}],
                                            "Values": [[lit(v)["expr"]] for v in values]}}}]}


def title_objs(text, size=11):
    off = {"subTitle": [{"properties": {"show": lit(False)}}]}
    if not text:
        return dict(off, title=[{"properties": {"show": lit(False)}}])
    return dict(off, title=[{"properties": {"show": lit(True), "text": lit(text), "fontSize": lit(size),
                                            "fontColor": colour(INK), "bold": lit(True)}}])


class Page:
    def __init__(self, name, display):
        self.name, self.display, self.visuals, self.n = name, display, [], 0

    def add(self, vtype, x, y, wdt, hgt, query=None, objects=None, title=None, filters=None, extra_vco=None,
            sort=None):
        self.n += 1
        v = {"visualType": vtype}
        if query:
            v["query"] = {"queryState": {role: {"projections": ps} for role, ps in query.items()}}
            if sort:
                v["query"]["sortDefinition"] = {"sort": [{"field": f, "direction": d} for f, d in sort],
                                                "isDefaultSort": True}
        if objects:
            v["objects"] = objects
        vco = title_objs(title) if vtype not in ("textbox", "shape") else {}
        if extra_vco:
            vco.update(extra_vco)
        if vco:
            v["visualContainerObjects"] = vco
        v["drillFilterOtherVisuals"] = True
        c = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.4.0/schema.json",
             "name": "%s_v%02d" % (self.name, self.n),
             "position": {"x": x, "y": y, "z": self.n * 1000, "width": wdt, "height": hgt, "tabOrder": self.n * 1000},
             "visual": v}
        if filters:
            c["filterConfig"] = {"filters": filters}
        self.visuals.append(c)

    def text(self, x, y, wdt, hgt, runs_by_para):
        paras = []
        for runs in runs_by_para:
            paras.append({"textRuns": [{"value": t, "textStyle": st} for t, st in runs]})
        self.add("textbox", x, y, wdt, hgt, objects={"general": [{"properties": {"paragraphs": paras}}]})

    def slicer(self, x, y, wdt, hgt, field, title, default, dropdown=True):
        objs = {"data": [{"properties": {"mode": lit("Dropdown" if dropdown else "Basic")}}],
                "selection": [{"properties": {"singleSelect": lit(True), "strictSingleSelect": lit(True)}}],
                "header": [{"properties": {"show": lit(True), "text": lit(title), "fontColor": colour(SLATE),
                                           "textSize": lit(9)}}],
                "general": [{"properties": {"filter": {"filter": in_filter(
                    field["Column"]["Expression"]["SourceRef"]["Entity"], field["Column"]["Property"], [default])}}}]}
        self.add("slicer", x, y, wdt, hgt, query={"Values": [proj(field)]}, objects=objs, title=None)


def card(page, x, y, wdt, hgt, m, label):
    page.add("card", x, y, wdt, hgt, query={"Values": [proj(mea(m), label)]},
             objects={"labels": [{"properties": {"fontSize": lit(26), "color": colour(INK)}}],
                      "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(9),
                                                         "color": colour(SLATE)}}]},
             title=None, extra_vco={"background": [{"properties": {"show": lit(True), "color": colour("#F3F6F7")}}]})


H1 = {"fontFamily": "Segoe UI Semibold", "fontSize": "20pt", "color": INK}
SUB = {"fontFamily": "Segoe UI", "fontSize": "10pt", "color": SLATE}
NOTE = {"fontFamily": "Segoe UI", "fontSize": "8pt", "color": MUTED}
BODY = {"fontFamily": "Segoe UI", "fontSize": "10pt", "color": INK}
BOLD = {"fontFamily": "Segoe UI Semibold", "fontSize": "10pt", "color": INK}
SOURCE = ("Source: Public Health Agency of Canada, Canadian Chronic Disease Surveillance System (CCDSS), Public Health "
          "Infobase, read 8 October 2026. Rates are age-standardized; fiscal years (2023 = 2023-24). "
          "Open Government Licence - Canada.")


def build_pages():
    pages = []

    # ---- page 1: the province's caseload
    p = Page("overview", "Caseload")
    p.text(24, 12, 860, 70, [[("Which chronic conditions will fill the caseload?", H1)],
                             [("Five conditions tracked by the national surveillance system. Pick a province; "
                               "every number below is for that province and the sex chosen.", SUB)]])
    p.slicer(900, 16, 200, 56, col("Geography", "Geography"), "Province or territory", "British Columbia")
    p.slicer(1112, 16, 144, 56, col("Rates", "Sex"), "Sex", "Both sexes")
    card(p, 24, 96, 300, 96, "People added in 10 yrs", "More people with diabetes than 10 years earlier")
    card(p, 340, 96, 300, 96, "Prevalence, latest", "Diabetes, age-standardized prevalence (%)")
    card(p, 656, 96, 300, 96, "Gap to Canada (pts)", "Diabetes, points above or below Canada")
    card(p, 972, 96, 284, 96, "New cases, latest", "New diabetes cases in the latest year")
    diabetes = [{"name": "f_diab_%d" % i, "field": col("Condition", "Condition"), "type": "Categorical",
                 "filter": in_filter("Condition", "Condition", ["Diabetes"])} for i in range(4)]
    for i, v in enumerate(p.visuals[-4:]):
        v["filterConfig"] = {"filters": [diabetes[i]]}
    p.add("tableEx", 24, 208, 760, 268,
          query={"Values": [proj(col("Condition", "Condition"), "Condition"),
                            proj(mea("People living with it"), "People living with it"),
                            proj(mea("People added in 10 yrs"), "Added in 10 years"),
                            proj(mea("Prevalence, latest"), "Prevalence %"),
                            proj(mea("Gap to Canada (pts)"), "vs Canada (pts)"),
                            proj(mea("Incidence change, 10 yrs (%)"), "New-case rate, 10-yr change")]},
          sort=[(mea("People added in 10 yrs"), "Descending")],
          objects={"columnHeaders": [{"properties": {"fontColor": colour(SLATE), "backColor": colour(PAPER),
                                                     "fontSize": lit(9), "bold": lit(True)}}],
                   "values": [{"properties": {"fontSize": lit(10), "fontColorPrimary": colour(INK),
                                              "backColorPrimary": colour(PAPER), "backColorSecondary": colour("#F6F8F9")}}],
                   "grid": [{"properties": {"gridHorizontal": lit(True), "gridHorizontalColor": colour(RULE),
                                            "rowPadding": lit(5)}}],
                   "total": [{"properties": {"totals": lit(False)}}]},
          title="Who the province is caring for, latest year")
    p.add("clusteredBarChart", 800, 208, 456, 268,
          query={"Category": [proj(col("Condition", "Condition"))],
                 "Y": [proj(mea("People added in 10 yrs"), "People added in 10 years")]},
          sort=[(mea("People added in 10 yrs"), "Descending")],
          objects={"dataPoint": [{"properties": {"fill": colour(TEAL)}}],
                   "labels": [{"properties": {"show": lit(True), "color": colour(INK), "fontSize": lit(9),
                                              "labelDisplayUnits": lit(1000), "labelPrecision": lit(0)}}],
                   "valueAxis": [{"properties": {"show": lit(False)}}],
                   "categoryAxis": [{"properties": {"fontSize": lit(9), "labelColor": colour(SLATE)}}]},
          title="Growth in people living with each condition, 10 years")
    p.add("lineChart", 24, 490, 1232, 198,
          query={"Category": [proj(col("Rates", "Year"))],
                 "Series": [proj(col("Condition", "Condition"))],
                 "Y": [proj(mea("Prevalence (ASR %)"), "Prevalence %")]},
          objects={"categoryAxis": [{"properties": {"fontSize": lit(9), "labelColor": colour(SLATE),
                                                    "showAxisTitle": lit(False)}}],
                   "valueAxis": [{"properties": {"fontSize": lit(9), "labelColor": colour(SLATE),
                                                 "showAxisTitle": lit(False), "gridlineColor": colour(RULE)}}],
                   "legend": [{"properties": {"show": lit(True), "position": lit("Right"), "fontSize": lit(9)}}]},
          title="Age-standardized prevalence (%), 2000 to latest")
    p.text(24, 694, 1232, 22, [[(SOURCE, NOTE)]])
    pages.append(p)

    # ---- page 2: provinces compared
    p = Page("provinces", "Provinces compared")
    p.text(24, 12, 860, 70, [[("Is the province above or below the rest of Canada?", H1)],
                             [("Age-standardized rates remove differences in age structure, so provinces can be "
                               "compared fairly. British Columbia is highlighted.", SUB)]])
    p.slicer(900, 16, 200, 56, col("Condition", "Condition"), "Condition", "Diabetes")
    p.slicer(1112, 16, 144, 56, col("Rates", "Sex"), "Sex", "Both sexes")
    prov_only = {"name": "f_prov", "field": col("Geography", "Level"), "type": "Categorical",
                 "filter": in_filter("Geography", "Level", ["Province/territory"])}
    p.add("clusteredBarChart", 24, 96, 600, 420,
          query={"Category": [proj(col("Geography", "Geography"))],
                 "Y": [proj(mea("Prevalence, latest"), "Prevalence %, latest year")]},
          sort=[(mea("Prevalence, latest"), "Descending")],
          objects={"dataPoint": [{"properties": {"fill": colour(TEAL_LIGHT)}},
                                 {"properties": {"fill": {"solid": {"color": {"expr": mea("Bar colour")}}}},
                                  "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}],
                   "labels": [{"properties": {"show": lit(True), "color": colour(INK), "fontSize": lit(9)}}],
                   "valueAxis": [{"properties": {"show": lit(False)}}],
                   "categoryAxis": [{"properties": {"fontSize": lit(9), "labelColor": colour(SLATE)}}]},
          filters=[prov_only], title="Age-standardized prevalence (%), latest year")
    p.add("lineChart", 640, 96, 616, 260,
          query={"Category": [proj(col("Rates", "Year"))],
                 "Y": [proj(mea("Prevalence (ASR %)"), "British Columbia"),
                       proj(mea("Prevalence, Canada"), "Canada")]},
          objects={"dataPoint": [{"properties": {"fill": colour(AMBER)}, "selector": {"metadata": "Rates.Prevalence (ASR %)"}},
                                 {"properties": {"fill": colour(CANADA_GREY)}, "selector": {"metadata": "Rates.Prevalence, Canada"}}],
                   "categoryAxis": [{"properties": {"fontSize": lit(9), "showAxisTitle": lit(False)}}],
                   "valueAxis": [{"properties": {"fontSize": lit(9), "showAxisTitle": lit(False),
                                                 "gridlineColor": colour(RULE)}}],
                   "legend": [{"properties": {"show": lit(True), "position": lit("Top"), "fontSize": lit(9)}}]},
          filters=[{"name": "f_bc", "field": col("Geography", "Geography"), "type": "Categorical",
                    "filter": in_filter("Geography", "Geography", ["British Columbia"])}],
          title="British Columbia against Canada, prevalence (%)")
    p.add("lineChart", 640, 372, 616, 260,
          query={"Category": [proj(col("Rates", "Year"))],
                 "Series": [proj(col("Geography", "GeoShort"))],
                 "Y": [proj(mea("Incidence (ASR per 100k)"), "New cases per 100,000")]},
          objects={"categoryAxis": [{"properties": {"fontSize": lit(9), "showAxisTitle": lit(False)}}],
                   "valueAxis": [{"properties": {"fontSize": lit(9), "showAxisTitle": lit(False),
                                                 "gridlineColor": colour(RULE)}}],
                   "legend": [{"properties": {"show": lit(True), "position": lit("Right"), "fontSize": lit(8)}}]},
          filters=[{"name": "f_big", "field": col("Geography", "GeoShort"), "type": "Categorical",
                    "filter": in_filter("Geography", "GeoShort", ["BC", "AB", "ON", "QC", "CAN"])}],
          title="New cases per 100,000 (age-standardized), largest provinces and Canada")
    bc_only = [{"name": "f_bc2", "field": col("Geography", "Geography"), "type": "Categorical",
                "filter": in_filter("Geography", "Geography", ["British Columbia"])}]
    card(p, 24, 532, 190, 156, "Prevalence, latest", "British Columbia, latest (%)")
    card(p, 229, 532, 190, 156, "Prevalence, Canada latest", "Canada, latest (%)")
    card(p, 434, 532, 190, 156, "Province rank", "BC's rank of 13 (1 = highest)")
    for v in p.visuals[-3:]:
        v["filterConfig"] = {"filters": [dict(bc_only[0], name="f_bc_%s" % v["name"])]}
    p.text(640, 648, 616, 40, [[(SOURCE, NOTE)]])
    pages.append(p)

    # ---- page 3: how the numbers are made
    p = Page("method", "How the numbers are made")
    p.text(24, 12, 1232, 70, [[("How the numbers are made", H1)],
                              [("Each condition is a case definition run on provincial health administrative data: "
                                "hospital discharges and physician claims, linked to the insurance registry.", SUB)]])
    p.add("tableEx", 24, 96, 820, 300,
          query={"Values": [proj(col("Condition", "Condition"), "Condition"),
                            proj(col("Condition", "CaseDefinition"), "Case definition"),
                            proj(col("Condition", "Ages"), "Ages"),
                            proj(col("Condition", "Prevalence"), "Prevalence counted")]},
          objects={"columnHeaders": [{"properties": {"fontColor": colour(SLATE), "fontSize": lit(9), "bold": lit(True)}}],
                   "values": [{"properties": {"fontSize": lit(9), "wordWrap": lit(True)}}],
                   "total": [{"properties": {"totals": lit(False)}}],
                   "grid": [{"properties": {"gridHorizontalColor": colour(RULE), "rowPadding": lit(6)}}],
                   "columnWidth": [{"properties": {"value": lit(470)},
                                    "selector": {"metadata": "Condition.CaseDefinition"}},
                                   {"properties": {"value": lit(110)},
                                    "selector": {"metadata": "Condition.Condition"}}]},
          title="Case definitions (PHAC, 2024)")
    p.add("lineClusteredColumnComboChart", 24, 412, 820, 270,
          query={"Category": [proj(col("Rates", "Year"))],
                 "Y": [proj(mea("People living with it, by year"), "People living with diabetes")],
                 "Y2": [proj(mea("Prevalence (ASR %)"), "Age-standardized prevalence (%)")]},
          objects={"dataPoint": [{"properties": {"fill": colour(TEAL)},
                                  "selector": {"metadata": "Rates.People living with it, by year"}},
                                 {"properties": {"fill": colour(AMBER)},
                                  "selector": {"metadata": "Rates.Prevalence (ASR %)"}}],
                   "categoryAxis": [{"properties": {"fontSize": lit(9), "showAxisTitle": lit(False)}}],
                   "valueAxis": [{"properties": {"fontSize": lit(9), "showAxisTitle": lit(False),
                                                 "gridlineColor": colour(RULE), "secShow": lit(True)}}],
                   "legend": [{"properties": {"show": lit(True), "position": lit("Top"), "fontSize": lit(9)}}]},
          filters=[{"name": "f_m_bc", "field": col("Geography", "Geography"), "type": "Categorical",
                    "filter": in_filter("Geography", "Geography", ["British Columbia"])},
                   {"name": "f_m_diab", "field": col("Condition", "Condition"), "type": "Categorical",
                    "filter": in_filter("Condition", "Condition", ["Diabetes"])}],
          title="Count and rate tell different stories: diabetes in British Columbia")
    p.text(864, 96, 392, 560, [
        [("Reading the brief", BOLD)],
        [("Prevalence is the share of the population who meet the case definition, counted for life for most "
          "conditions.", BODY)],
        [("Age-standardized rates use one standard population, so an older province does not look sicker only "
          "because it is older. Use them to compare places and years.", BODY)],
        [("People living with it is a count, not a rate. It grows with ageing and population even when the rate "
          "is flat, and it is the number that sizes clinics and budgets.", BODY)],
        [(" ", BODY)],
        [("What it cannot tell you", BOLD)],
        [("Administrative data see people who used care. Someone with undiagnosed diabetes, or treated only "
          "outside the insured system, is not counted. Definitions are validated per condition by PHAC.", BODY)],
        [("Blank years are years the province did not report; they are left blank, never filled.", BODY)],
    ])
    p.text(24, 694, 1232, 22, [[(SOURCE, NOTE)]])
    pages.append(p)
    return pages


def build_report():
    if os.path.isdir(RP):
        shutil.rmtree(RP)
    wj(os.path.join(RP, "definition.pbir"), {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": "../%s.SemanticModel" % NAME}}})
    d = os.path.join(RP, "definition")
    wj(os.path.join(d, "version.json"), {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0"})
    base = "Fluent2-CY26SU08"
    src = os.path.join(r"C:\Program Files\Microsoft Power BI Desktop\bin\WebView2Resources\minerva\sharedresources\BaseThemes",
                       base + ".json")
    os.makedirs(os.path.join(RP, "StaticResources", "SharedResources", "BaseThemes"), exist_ok=True)
    shutil.copyfile(src, os.path.join(RP, "StaticResources", "SharedResources", "BaseThemes", base + ".json"))
    theme = {"name": "Brief", "dataColors": [TEAL, AMBER, "#5B7F95", "#A3B86C", "#8E6C8A", "#C9A227", "#4F9D9A",
                                              CANADA_GREY],
             "foreground": INK, "background": PAPER, "tableAccent": TEAL,
             "textClasses": {"label": {"fontFace": "Segoe UI", "color": SLATE},
                             "title": {"fontFace": "Segoe UI Semibold", "color": INK}}}
    wj(os.path.join(RP, "StaticResources", "RegisteredResources", "Brief.json"), theme)
    wj(os.path.join(d, "report.json"), {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/1.2.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": base, "reportVersionAtImport": "5.68",
                          "type": "SharedResources"},
            "customTheme": {"name": "Brief.json", "reportVersionAtImport": "5.68",
                            "type": "RegisteredResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": base, "path": "BaseThemes/%s.json" % base, "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "Brief.json", "path": "Brief.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True, "useEnhancedTooltips": True}})
    pages = build_pages()
    wj(os.path.join(d, "pages", "pages.json"), {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name})
    for p in pages:
        wj(os.path.join(d, "pages", p.name, "page.json"), {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.3.0/schema.json",
            "name": p.name, "displayName": p.display, "displayOption": "FitToPage", "height": 720, "width": 1280})
        for v in p.visuals:
            wj(os.path.join(d, "pages", p.name, "visuals", v["name"], "visual.json"), v)
    return pages


build_model()
pages = build_report()
wj(os.path.join(OUT, NAME + ".pbip"), {
    "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
    "version": "1.0", "artifacts": [{"report": {"path": NAME + ".Report"}}], "settings": {"enableAutoRecovery": True}})
w(os.path.join(OUT, ".gitignore"), "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
print("wrote", OUT)
for p in pages:
    print("  page %-12s %d visuals" % (p.name, len(p.visuals)))
print("  measures", len(MEASURES))

import base64, subprocess, tempfile, pathlib
import pytest
pytest.importorskip("weasyprint"); pytest.importorskip("geopandas")
import report
from synthetic import synthetic_exposure

@pytest.fixture()
def model(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    m = Model(synthetic_exposure(400)); m.run(28.575, 77.33, 10, 7.0, strike=20, dip=60, rake=90)
    return m

def ctx_for(model, **kw):
    return report.build_context(model, {"name": "Test city"}, names=False, **kw)

def pdf_text(pdf):
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "r.pdf"; p.write_bytes(pdf)
        return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout

def test_pdf_has_scenario_summary_caveats_and_attribution(model):
    import uncertainty as U
    sn = model.mc_snapshot(); mc = U.report(sn, U.run_mc(model, n=15, seed=1, snapshot=sn))
    pdf = report.to_pdf(ctx_for(model, mc=mc, access={"share_within_30min": .9, "share_no_access": .02, "median_minutes": 7.5, "hospitals": 5, "hospitals_may_not_work": 2, "streets": 100, "streets_likely_blocked": 4},
                                dispatch=[{"order": 1, "team": "fire station 1", "facility": "F1", "cell_rank": 2, "people_at_risk": 1234, "travel_min": 5.5, "p_route_open": .9}]))
    assert pdf.startswith(b"%PDF")
    t = pdf_text(pdf)
    for needle in ("Magnitude 7.0", "Test city", "People at risk", "Priority neighbourhoods", "Hospital access", "Suggested dispatch order", "Worst case",
                   "UNCALIBRATED", "UNVERIFIED", "Overture Maps Foundation", "range", "Estimated deaths"):
        assert needle in t, needle

def test_pdf_embeds_the_map_image_and_works_without_optional_parts(model):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    bare = report.render_html(ctx_for(model))
    assert "Hospital access" not in bare and "dispatch order" not in bare and "<img" not in bare
    assert "data:image/png;base64" in report.render_html(ctx_for(model, image_png=png))
    assert report.to_pdf(ctx_for(model, image_png=png)).startswith(b"%PDF")

def test_csv_sections(model):
    rows = report.to_csv(ctx_for(model, dispatch=[{"order": 1, "team": "t", "facility": "f", "cell_rank": 1, "people_at_risk": 5, "travel_min": 1.0, "p_route_open": 1.0}])).splitlines()
    assert rows[0].startswith("section,rank,place") and sum(r.startswith("priority") for r in rows) == len([f for f in model.cells if f["properties"]["rank"]])
    assert any(r.startswith("dispatch") for r in rows)

def test_html_escapes_place_names(model):
    c = ctx_for(model); c["cells"][0]["name"] = "<script>x</script>"
    assert "<script>x" not in report.render_html(c) and "&lt;script&gt;" in report.render_html(c)

def test_report_for_a_cyclone_has_no_earthquake_wording(model):
    import hazards.cyclone as CY
    model.run_hazard(CY.CycloneHazard(), dict(lat=28.575, lon=77.33, heading_deg=45, dp_hpa=90, rmax_km=20))
    h = report.render_html(ctx_for(model))
    assert "Cyclone scenario" in h and "Peak gust" in h and "Magnitude" not in h and "placeholders" in h
    assert report.to_pdf(ctx_for(model)).startswith(b"%PDF")

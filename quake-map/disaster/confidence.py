"""'How much to trust this' items for the UI and report (MASTER_PLAN 6.4). Status flags are read from the live data and files,
not hand-written: placeholder curves, unverified casualty rates, how much of the area has measured storeys, which population
source was used, and the recorded replay scores."""
import json, pathlib

HERE = pathlib.Path(__file__).resolve().parent

def build(model, area_meta=None, data_dir=None):
    import priors
    items = []
    frag = json.loads(priors.path("fragility.json", data_dir).read_text())
    cal = not frag["status"].startswith("UNCALIBRATED")
    items.append({"topic": "Building strength (fragility curves)", "level": "good" if cal and frag["status"].startswith("CALIBRATED") else "caution" if cal else "poor",
                  "text": frag["status"].capitalize() + ". " + ("; ".join(f"{k}: {v}" for k, v in frag.get("class_status", {}).items()) if cal else "Not fitted to data; damage numbers are relative, not predictions.")})
    cas = json.loads(priors.path("casualty_params.json", data_dir).read_text())
    items.append({"topic": "Casualty rates", "level": "good" if cas.get("verified") else "poor",
                  "text": "Verified against the Hazus manual." if cas.get("verified") else "Unverified placeholders in the Hazus structure; compare with the PAGER figure."})
    st = model.stats if model is not None else {}
    if "levels_measured_share" in st:
        sh = st["levels_measured_share"]
        items.append({"topic": "Storeys", "level": "good" if sh >= 0.8 else "caution" if sh >= 0.5 else "poor",
                      "text": f"{sh:.0%} of buildings have measured storeys ({st.get('height_source')}); the rest are estimated. Satellite storeys agree with mapped labels within one storey for about 69% of labelled Noida buildings."})
    if "pop_src" in st:
        ok = st["pop_src"] == "GHS-POP"
        items.append({"topic": "People", "level": "caution" if ok else "poor", "text": ("Scaled to GHS-POP (a modelled estimate)" if ok else "Typical-occupancy guess without a population raster (probably far too high)") + "."})
    items.append({"topic": "Construction types", "level": "caution", "text": "Probabilities from a documented rule with an assumed census prior; not measured."})
    f = HERE / "docs" / "validation_summary.json"
    if f.exists():
        v = json.loads(f.read_text())
        parts, level = [], "caution"
        for ev, d in v.items():
            r = [x for x in d["rows"] if x["shaking"] == "our shaking model" and "no visible" in x["outcome"] and x["positives"] >= 5]
            if r:
                best = max(r, key=lambda x: x["positives"])
                parts.append(f"{ev}: ranking skill {best['auc']} (0.5 = none, {best['positives']} damaged buildings, 90% interval {best['ci90']})")
        if parts and all(float(p.split("skill ")[1].split(" ")[0]) < 0.7 for p in parts):
            level = "poor"
        items.append({"topic": "Past-earthquake replays", "level": level, "text": "; ".join(parts) + ". Weak or inconclusive evidence; see docs/validation.md."})
    else:
        items.append({"topic": "Past-earthquake replays", "level": "poor", "text": "Not run."})
    return items

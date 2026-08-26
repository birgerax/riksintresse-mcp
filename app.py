import os
import requests
from typing import Optional
from flask import Flask, request, jsonify

BOVERKET_BASE = (
    "https://gis2.boverket.se/arcgis/rest/services"
    "/Riksintressen___Februari_2025_WMS/MapServer"
)
TIMEOUT = 20

RIKSINTRESSE_TYPER = {
    "Totalförsvar":           ("Försvarsmakten / MSB",         "MB 3:9"),
    "Kommunikationer":        ("Trafikverket",                  "MB 3:8"),
    "Energiförsörjning":      ("Energimyndigheten",             "MB 3:8"),
    "Vindbruk":               ("Energimyndigheten",             "MB 3:8"),
    "Telekommunikation":      ("PTS",                           "MB 3:8"),
    "Naturvård":              ("Naturvårdsverket",              "MB 3:6"),
    "Friluftsliv":            ("Naturvårdsverket",              "MB 3:6"),
    "Kulturmiljövård":        ("Riksantikvarieämbetet",         "MB 3:6"),
    "Mineralutvinning":       ("SGU",                           "MB 3:7"),
    "Industri":               ("Tillväxtverket",                "MB 3:8"),
    "Turism och friluftsliv": ("Naturvårdsverket / Tillväxtv.", "MB 4:1-2"),
    "Yrkesfiske":             ("Havs- och vattenmyndigheten",   "MB 3:5"),
    "Rennäring":              ("Sametinget",                    "MB 3:5"),
    "Obruten kust":           ("Riksdagen",                     "MB 4:3"),
    "Högexploaterad kust":    ("Riksdagen",                     "MB 4:4"),
    "Fjällen":                ("Riksdagen",                     "MB 4:5"),
    "Älvar":                  ("Riksdagen",                     "MB 4:6"),
}

def _identify(lat, lon):
    params = {
        "f": "json", "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint", "sr": "4326",
        "layers": "all", "tolerance": 5,
        "mapExtent": f"{lon-0.05},{lat-0.05},{lon+0.05},{lat+0.05}",
        "imageDisplay": "800,600,96", "returnGeometry": "false",
    }
    try:
        r = requests.get(f"{BOVERKET_BASE}/identify", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("results", [])
    except requests.RequestException as e:
        return [{"_error": str(e)}]

def _format_attrs(attrs):
    skip = {"objectid", "shape_area", "shape_length", "globalid"}
    return [f"  - **{k}**: {v}" for k, v in attrs.items()
            if v is not None and k.lower() not in skip]

def list_riksintresse_typer():
    mb3 = {k: v for k, v in RIKSINTRESSE_TYPER.items() if "4:" not in v[1]}
    mb4 = {k: v for k, v in RIKSINTRESSE_TYPER.items() if "4:" in v[1]}
    rows = ["## Riksintressetyper\n", "### MB 3 kap – myndighetsbeslutade\n",
            "| Riksintresse | Myndighet | Lagrum |", "|---|---|---|"]
    for n, (m, l) in mb3.items():
        rows.append(f"| {n} | {m} | {l} |")
    rows += ["\n### MB 4 kap – riksdagsbeslutade\n", "| Riksintresse | Lagrum |", "|---|---|"]
    for n, (_, l) in mb4.items():
        rows.append(f"| {n} | {l} |")
    rows.append("\n*Totalförsvar har överordnad ställning (MB 3:10).*")
    return "\n".join(rows)

def get_riksintressen_vid_koordinat(latitud, longitud):
    features = _identify(latitud, longitud)
    if features and "_error" in features[0]:
        return f"⚠️ Fel: {features[0]['_error']}"
    if not features:
        return f"Inga riksintressen vid {latitud:.5f}°N, {longitud:.5f}°E."
    by_layer = {}
    for f in features:
        by_layer.setdefault(f.get("layerName", "Okänd"), []).append(f.get("attributes", {}))
    lines = [f"## Riksintressen vid {latitud:.5f}°N, {longitud:.5f}°E\n",
             f"**{len(features)} förekomster** i **{len(by_layer)} kategorier**\n"]
    for layer, attrs_list in sorted(by_layer.items()):
        lines.append(f"\n### {layer}")
        for attrs in attrs_list:
            rows = _format_attrs(attrs)
            lines.extend(rows if rows else ["  *(inga ytterligare attribut)*"])
    if len(by_layer) > 1:
        keys = sorted(by_layer.keys())
        pairs = [f"- **{a}** ↔ **{b}**" for i, a in enumerate(keys) for b in keys[i+1:]]
        lines += ["\n### ⚠️ Potentiella konflikter\n"] + pairs
    lines.append("\n*Källa: Boverkets riksintressekarta*")
    return "\n".join(lines)

def analysera_konflikter_i_omrade(lat_min, lon_min, lat_max, lon_max):
    clat, clon = (lat_min + lat_max) / 2, (lon_min + lon_max) / 2
    features = _identify(clat, clon)
    existing = {f.get("layerName") for f in features}
    for clat_c, clon_c in [(lat_min+0.1, lon_min+0.1), (lat_max-0.1, lon_min+0.1),
                            (lat_min+0.1, lon_max-0.1), (lat_max-0.1, lon_max-0.1)]:
        for f in _identify(clat_c, clon_c):
            if f.get("layerName") not in existing and "_error" not in f:
                features.append(f); existing.add(f.get("layerName"))
    if features and "_error" in features[0]:
        return f"⚠️ Fel: {features[0]['_error']}"
    layer_names = sorted({f.get("layerName", "Okänd") for f in features if "_error" not in f})
    lines = ["## Riksintresseanalys\n",
             f"**Koordinater:** {lat_min:.2f}–{lat_max:.2f}°N, {lon_min:.2f}–{lon_max:.2f}°E"]
    if not layer_names:
        return "\n".join(lines) + "\nInga riksintressen hittades."
    lines.append("### Riksintressetyper i området\n")
    for name in layer_names:
        info = next(((m, l) for k, (m, l) in RIKSINTRESSE_TYPER.items()
                     if k.lower() in name.lower()), None)
        lines.append(f"- **{name}**" + (f" – {info[0]} ({info[1]})" if info else ""))
    if len(layer_names) > 1:
        pairs = []
        for i, a in enumerate(layer_names):
            for b in layer_names[i+1:]:
                tag = " 🔴 *(totalförsvar – överordnat)*" if any("försvar" in x.lower() for x in [a, b]) else ""
                pairs.append(f"- **{a}** ↔ **{b}**{tag}")
        lines += [f"\n### ⚠️ Avvägningssituationer ({len(pairs)} par)\n"] + pairs
    lines.append("\n*Källa: Boverkets riksintressekarta*")
    return "\n".join(lines)

def forklara_riksintressesystemet(fraga=None):
    text = """## Riksintressesystemet\n
| Kapitel | Innehåll |\n|---|---|\n| MB 3 kap | Myndighetspekade riksintressen |
| MB 4 kap | Kust, fjäll, älvar |\n\n- Totalförsvar har överordnad ställning (MB 3:10)"""
    if fraga:
        text += f"\n\n### Din fråga: {fraga}"
    return text

TOOLS = {
    "list_riksintresse_typer": {
        "fn": lambda _: list_riksintresse_typer(),
        "description": "Lista alla riksintressetyper med ansvarig myndighet och lagrum.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    "get_riksintressen_vid_koordinat": {
        "fn": lambda p: get_riksintressen_vid_koordinat(p["latitud"], p["longitud"]),
        "description": "Hämta riksintressen vid en koordinat (WGS84).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "latitud":  {"type": "number"},
                "longitud": {"type": "number"},
            },
            "required": ["latitud", "longitud"],
        },
    },
    "analysera_konflikter_i_omrade": {
        "fn": lambda p: analysera_konflikter_i_omrade(p["lat_min"], p["lon_min"], p["lat_max"], p["lon_max"]),
        "description": "Identifiera riksintressekonflikter i ett område.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat_min": {"type": "number"}, "lon_min": {"type": "number"},
                "lat_max": {"type": "number"}, "lon_max": {"type": "number"},
            },
            "required": ["lat_min", "lon_min", "lat_max", "lon_max"],
        },
    },
    "forklara_riksintressesystemet": {
        "fn": lambda p: forklara_riksintressesystemet(p.get("fraga")),
        "description": "Förklarar riksintressesystemets rättsliga grund.",
        "inputSchema": {"type": "object", "properties": {"fraga": {"type": "string"}}, "required": []},
    },
}

app = Flask(__name__)

@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/mcp", methods=["OPTIONS"])
def mcp_preflight():
    return ("", 204)

@app.route("/mcp", methods=["GET"])
def mcp_healthcheck():
    return jsonify({"status": "ok", "server": "riksintresse-mcp"}), 200

@app.route("/mcp", methods=["POST"])
def mcp_endpoint():
    body   = request.get_json(force=True)
    method = body.get("method", "")
    req_id = body.get("id")

    if method == "initialize":
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "riksintresse-mcp", "version": "1.0"},
        }})
    if method.startswith("notifications/"):
        return ("", 204)
    if method == "tools/list":
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
            {"name": n, "description": m["description"], "inputSchema": m["inputSchema"]}
            for n, m in TOOLS.items()
        ]}})
    if method == "tools/call":
        params = body.get("params", {})
        name   = params.get("name", "")
        args   = params.get("arguments", {})
        if name not in TOOLS:
            return jsonify({"jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32601, "message": f"Verktyg '{name}' finns inte"}})
        try:
            result_text = TOOLS[name]["fn"](args)
        except Exception as e:
            result_text = f"⚠️ Fel: {e}"
        return jsonify({"jsonrpc": "2.0", "id": req_id,
                        "result": {"content": [{"type": "text", "text": result_text}]}})
    return jsonify({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Okänd metod: {method}"}})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
import os
from itertools import combinations

import requests
from flask import Flask, jsonify, request

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


# ── Interna hjälpfunktioner ───────────────────────────────────────────────────

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


def _grid_sample(lat_min, lon_min, lat_max, lon_max, steps=4):
    """Scanna ett bounding box med ett steps×steps rutnät.

    Returnerar en dedupliserad lista av features – max ett objekt per
    unik layerName – vilket löser den statistiska svagheten i den gamla
    5-punkts-samplingen.
    """
    lat_step = (lat_max - lat_min) / max(steps - 1, 1)
    lon_step = (lon_max - lon_min) / max(steps - 1, 1)
    seen, out = set(), []
    for i in range(steps):
        for j in range(steps):
            lat = lat_min + i * lat_step
            lon = lon_min + j * lon_step
            for f in _identify(lat, lon):
                if "_error" not in f:
                    ln = f.get("layerName", "Okänd")
                    if ln not in seen:
                        seen.add(ln)
                        out.append(f)
    return out


def _lookup_kommun(kommunnamn):
    """Geokoda ett kommunnamn via Nominatim/OpenStreetMap."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{kommunnamn} kommun, Sverige",
                    "format": "json", "limit": 1},
            headers={"User-Agent": "riksintresse-mcp/2.0"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        res = r.json()
        if not res:
            return None
        hit = res[0]
        bb = hit.get("boundingbox", [])
        if len(bb) == 4:
            lat_min, lat_max = float(bb[0]), float(bb[1])
            lon_min, lon_max = float(bb[2]), float(bb[3])
        else:
            lat, lon = float(hit["lat"]), float(hit["lon"])
            lat_min, lat_max = lat - 0.2, lat + 0.2
            lon_min, lon_max = lon - 0.3, lon + 0.3
        return {
            "display": hit.get("display_name", kommunnamn),
            "lat": float(hit["lat"]), "lon": float(hit["lon"]),
            "lat_min": lat_min, "lat_max": lat_max,
            "lon_min": lon_min, "lon_max": lon_max,
        }
    except requests.RequestException as e:
        return {"_error": str(e)}


def _format_attrs(attrs):
    skip = {"objectid", "shape_area", "shape_length", "globalid"}
    return [f"  - **{k}**: {v}" for k, v in attrs.items()
            if v is not None and k.lower() not in skip]



# ── Verktyg ───────────────────────────────────────────────────────────────────

def list_riksintresse_typer():
    mb3 = {k: v for k, v in RIKSINTRESSE_TYPER.items() if "4:" not in v[1]}
    mb4 = {k: v for k, v in RIKSINTRESSE_TYPER.items() if "4:" in v[1]}
    rows = ["## Riksintressetyper\n",
            "### MB 3 kap – myndighetsbeslutade\n",
            "| Riksintresse | Myndighet | Lagrum |", "|---|---|---|"]
    for n, (m, l) in mb3.items():
        rows.append(f"| {n} | {m} | {l} |")
    rows += ["\n### MB 4 kap – riksdagsbeslutade\n",
             "| Riksintresse | Lagrum |", "|---|---|"]
    for n, (_, l) in mb4.items():
        rows.append(f"| {n} | {l} |")
    rows.append("\n*Totalförsvar har överordnad ställning (MB 3:10).*")
    return "\n".join(rows)


def get_riksintressen_vid_koordinat(latitud, longitud):
    features = _identify(latitud, longitud)
    if features and "_error" in features[0]:
        return f"Fel: {features[0]['_error']}"
    if not features:
        return f"Inga riksintressen vid {latitud:.5f}N, {longitud:.5f}E."
    by_layer = {}
    for f in features:
        by_layer.setdefault(f.get("layerName", "Okänd"), []).append(
            f.get("attributes", {}))
    lines = [f"## Riksintressen vid {latitud:.5f}N, {longitud:.5f}E\n",
             f"**{len(features)} förekomster** i **{len(by_layer)} kategorier**\n"]
    for layer, attrs_list in sorted(by_layer.items()):
        lines.append(f"\n### {layer}")
        for attrs in attrs_list:
            rows = _format_attrs(attrs)
            lines.extend(rows if rows else ["  *(inga ytterligare attribut)*"])
    if len(by_layer) > 1:
        keys = sorted(by_layer.keys())
        pairs = [f"- **{a}** vs **{b}**"
                 for i, a in enumerate(keys) for b in keys[i+1:]]
        lines += ["\n### Potentiella konflikter\n"] + pairs
    lines.append("\n*Källa: Boverkets riksintressekarta*")
    return "\n".join(lines)


def analysera_konflikter_i_omrade(lat_min, lon_min, lat_max, lon_max, grid_steps=4):
    """Uppgraderad till grid-sampling (steps×steps punkter) istället för 5."""
    features = _grid_sample(lat_min, lon_min, lat_max, lon_max, steps=grid_steps)
    layer_names = sorted({f.get("layerName", "Okänd") for f in features})
    n_pts = grid_steps ** 2
    lines = ["## Riksintresseanalys\n",
             f"**Koordinater:** {lat_min:.2f}–{lat_max:.2f}N, "
             f"{lon_min:.2f}–{lon_max:.2f}E",
             f"**Sampling:** {grid_steps}×{grid_steps} = {n_pts} punkter\n"]
    if not layer_names:
        return "\n".join(lines) + "\nInga riksintressen hittades."
    lines.append("### Riksintressetyper i området\n")
    for ln in layer_names:
        info = next(((m, l) for k, (m, l) in RIKSINTRESSE_TYPER.items()
                     if k.lower() in ln.lower()), None)
        lines.append(f"- **{ln}**" + (f" – {info[0]} ({info[1]})" if info else ""))
    if len(layer_names) > 1:
        pairs = []
        for a, b in combinations(layer_names, 2):
            tag = (" *(totalförsvar – överordnat)*"
                   if any("försvar" in x.lower() for x in [a, b]) else "")
            pairs.append(f"- **{a}** vs **{b}**{tag}")
        lines += [f"\n### Avvägningssituationer ({len(pairs)} par)\n"] + pairs
    lines.append("\n*Källa: Boverkets riksintressekarta*")
    return "\n".join(lines)


def sampla_omrade_grid(lat_min, lon_min, lat_max, lon_max, grid_steps=4):
    """Exponerar grid-samplingsresultaten direkt som ett fristående verktyg."""
    features = _grid_sample(lat_min, lon_min, lat_max, lon_max, steps=grid_steps)
    layer_names = sorted({f.get("layerName", "Okänd") for f in features})
    n_pts = grid_steps ** 2
    lines = [f"## Gridsampling {grid_steps}×{grid_steps} ({n_pts} punkter)\n",
             f"**Område:** {lat_min:.3f}–{lat_max:.3f}N, "
             f"{lon_min:.3f}–{lon_max:.3f}E",
             f"**Unika riksintresselager:** {len(layer_names)}\n"]
    for ln in layer_names:
        lines.append(f"- {ln}")
    lines.append("\n*Källa: Boverkets riksintressekarta*")
    return "\n".join(lines)


def berakna_konfliktintensitet(lat_min, lon_min, lat_max, lon_max, grid_steps=4):
    """Beräknar konfliktscore 0–10 baserat på antal intressen, par och totalförsvar."""
    features = _grid_sample(lat_min, lon_min, lat_max, lon_max, steps=grid_steps)
    layer_names = sorted({f.get("layerName", "Okänd") for f in features})
    n = len(layer_names)
    if n == 0:
        return "Inga riksintressen hittades – konfliktnivå **0/10**."

    pairs = list(combinations(layer_names, 2))
    has_forsvar = any("försvar" in ln.lower() for ln in layer_names)

    # Poängmodell
    score = min(n * 1.5, 6.0)           # Grundpoäng per riksintresse (max 6)
    score += min(len(pairs) * 0.3, 3.0) # Parbonus (max 3)
    if has_forsvar:
        score = max(score, 9.0)         # Totalförsvar → minst 9
    score = min(round(score, 1), 10.0)

    if score >= 9:   lvl, em = "Maximal",  "🔴"
    elif score >= 7: lvl, em = "Hög",      "🟠"
    elif score >= 5: lvl, em = "Måttlig",  "🟡"
    elif score >= 3: lvl, em = "Låg",      "🟢"
    else:            lvl, em = "Minimal",  "⚪"

    lines = [f"## Konfliktintensitet {em} {score}/10 – {lvl}\n",
             f"**Område:** {lat_min:.2f}–{lat_max:.2f}N, {lon_min:.2f}–{lon_max:.2f}E",
             f"**Riksintressen:** {n} st · **Konfliktpar:** {len(pairs)} st",
             f"**Totalförsvar:** {'Ja – automatisk maxnivå (MB 3:10)' if has_forsvar else 'Nej'}\n",
             "### Riksintressen i området\n"]
    for ln in layer_names:
        lines.append(f"- {ln}")
    lines.append("\n*Källa: Boverkets riksintressekarta*")
    return "\n".join(lines)


def sok_riksintressen_i_kommun(kommunnamn, grid_steps=4):
    """Geokoda kommunnamn automatiskt och kör riksintresseanalys."""
    info = _lookup_kommun(kommunnamn)
    if info is None:
        return f"Hittade ingen kommun med namnet '{kommunnamn}'."
    if "_error" in info:
        return f"Fel vid geokodning: {info['_error']}"

    header = (f"## Riksintressen i {kommunnamn}\n"
              f"*Geokodad som: {info['display']}*\n"
              f"**Centroid:** {info['lat']:.4f}N, {info['lon']:.4f}E\n"
              f"**Bounding box:** {info['lat_min']:.3f}–{info['lat_max']:.3f}N, "
              f"{info['lon_min']:.3f}–{info['lon_max']:.3f}E\n")

    analys = analysera_konflikter_i_omrade(
        info["lat_min"], info["lon_min"],
        info["lat_max"], info["lon_max"],
        grid_steps=grid_steps,
    )
    # Plocka bort dubbel-rubriken från sub-funktionen
    body = "\n".join(analys.splitlines()[1:])
    return header + body



def forklara_riksintressesystemet(fraga=None):
    text = ("## Riksintressesystemet\n"
            "| Kapitel | Innehåll |\n|---|---|\n"
            "| MB 3 kap | Myndighetspekade riksintressen |\n"
            "| MB 4 kap | Kust, fjäll, älvar |\n\n"
            "- Totalförsvar har överordnad ställning (MB 3:10)")
    if fraga:
        text += f"\n\n### Din fråga: {fraga}"
    return text


# ── Verktygsregister ──────────────────────────────────────────────────────────

_BBOX_PROPS = {
    "lat_min": {"type": "number"}, "lon_min": {"type": "number"},
    "lat_max": {"type": "number"}, "lon_max": {"type": "number"},
    "grid_steps": {"type": "integer", "default": 4,
                   "description": "Rutnätets sida (4 = 4×4 = 16 punkter)."},
}
_BBOX_REQ = ["lat_min", "lon_min", "lat_max", "lon_max"]

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
                "latitud":  {"type": "number", "description": "Latitud (WGS84)."},
                "longitud": {"type": "number", "description": "Longitud (WGS84)."},
            },
            "required": ["latitud", "longitud"],
        },
    },
    "analysera_konflikter_i_omrade": {
        "fn": lambda p: analysera_konflikter_i_omrade(
            p["lat_min"], p["lon_min"], p["lat_max"], p["lon_max"],
            p.get("grid_steps", 4)),
        "description": (
            "Identifiera riksintressekonflikter i ett bounding box via "
            "grid-sampling (steps×steps punkter, default 4×4=16)."
        ),
        "inputSchema": {"type": "object", "properties": _BBOX_PROPS,
                        "required": _BBOX_REQ},
    },
    "sampla_omrade_grid": {
        "fn": lambda p: sampla_omrade_grid(
            p["lat_min"], p["lon_min"], p["lat_max"], p["lon_max"],
            p.get("grid_steps", 4)),
        "description": (
            "Scanna ett bounding box systematiskt och returnera alla unika "
            "riksintresselager. Löser svagheten med 5-punktssampling."
        ),
        "inputSchema": {"type": "object", "properties": _BBOX_PROPS,
                        "required": _BBOX_REQ},
    },
    "berakna_konfliktintensitet": {
        "fn": lambda p: berakna_konfliktintensitet(
            p["lat_min"], p["lon_min"], p["lat_max"], p["lon_max"],
            p.get("grid_steps", 4)),
        "description": (
            "Beräkna konfliktscore 0–10 för ett område. "
            "Totalförsvar ger automatiskt maxnivå. "
            "Ger en direkt begriplig bild för beslutsfattare."
        ),
        "inputSchema": {"type": "object", "properties": _BBOX_PROPS,
                        "required": _BBOX_REQ},
    },
    "sok_riksintressen_i_kommun": {
        "fn": lambda p: sok_riksintressen_i_kommun(
            p["kommunnamn"], p.get("grid_steps", 4)),
        "description": (
            "Sök riksintressen i en svensk kommun via kommunnamn. "
            "Geokodning sker automatiskt – koordinater behövs inte."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kommunnamn": {"type": "string",
                               "description": "Kommunens namn, t.ex. 'Kiruna'."},
                "grid_steps": {"type": "integer", "default": 4},
            },
            "required": ["kommunnamn"],
        },
    },
    "forklara_riksintressesystemet": {
        "fn": lambda p: forklara_riksintressesystemet(p.get("fraga")),
        "description": "Förklarar riksintressesystemets rättsliga grund.",
        "inputSchema": {"type": "object",
                        "properties": {"fraga": {"type": "string"}},
                        "required": []},
    },
}


# ── Flask-app ─────────────────────────────────────────────────────────────────

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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/mcp", methods=["GET"])
def mcp_healthcheck():
    return jsonify({"status": "ok", "server": "riksintresse-mcp", "version": "2.0"}), 200


@app.route("/mcp", methods=["POST"])
def mcp_endpoint():
    body   = request.get_json(force=True)
    method = body.get("method", "")
    req_id = body.get("id")

    if method == "initialize":
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "riksintresse-mcp", "version": "2.0"},
        }})
    if method.startswith("notifications/"):
        return ("", 204)
    if method == "tools/list":
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
            {"name": n, "description": m["description"],
             "inputSchema": m["inputSchema"]}
            for n, m in TOOLS.items()
        ]}})
    if method == "tools/call":
        params = body.get("params", {})
        name   = params.get("name", "")
        args   = params.get("arguments", {})
        if name not in TOOLS:
            return jsonify({"jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32601,
                                      "message": f"Verktyg '{name}' finns inte"}})
        try:
            result_text = TOOLS[name]["fn"](args)
        except Exception as e:
            result_text = f"Fel: {e}"
        return jsonify({"jsonrpc": "2.0", "id": req_id,
                        "result": {"content": [{"type": "text",
                                                "text": result_text}]}})
    return jsonify({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601,
                              "message": f"Okänd metod: {method}"}})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

"""Count Portuguese roundabouts per municipality from OpenStreetMap data.

Pipeline:
    1. fetch_roundabout_ways()   -> every OSM way tagged as a roundabout, with geometry
    2. merge_roundabout_ways()   -> one row per physical roundabout (ways sharing a node)
    3. assign_municipalities()   -> point-in-polygon join against official boundaries (CAOP)
    4. municipality_stats()      -> counts, per km2 and per 10k inhabitants

Usage:
    python roundabout.py --boundaries CAOP.gpkg@<mainland layer> --boundaries CAOP.gpkg@<Madeira layer> ...
"""
import argparse
import time
import unicodedata

import numpy as np
import pandas as pd
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Boxes used to tell apart homonymous municipalities (Lagoa, Calheta).
REGION_BOXES = {
    "Açores": (-32.0, 36.5, -24.0, 40.0),   # (min_lon, min_lat, max_lon, max_lat)
    "Madeira": (-17.5, 32.3, -16.0, 33.2),
}


def build_overpass_query(junctions=("roundabout", "circular"), timeout=600):
    """Query every roundabout way in Portugal (mainland + Azores + Madeira).

    Matching the country by ISO code avoids picking up any other area named "Portugal".
    """
    junction_regex = "|".join(junctions)
    return f"""
    [out:json][timeout:{timeout}];
    area["ISO3166-1"="PT"][admin_level=2]->.pt;
    way["junction"~"^({junction_regex})$"](area.pt);
    out geom;
    """


def fetch_roundabout_ways(query=None, retries=4, timeout=900):
    """Return the raw Overpass elements, retrying on rate limits and server errors."""
    query = query or build_overpass_query()
    for attempt in range(retries + 1):
        try:
            response = requests.post(OVERPASS_URL, data={"data": query}, timeout=timeout)
            if response.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"Overpass busy ({response.status_code})")
            response.raise_for_status()
            return response.json()["elements"]
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt + 1))


def merge_roundabout_ways(elements):
    """Collapse the OSM ways of the same roundabout into a single point.

    A roundabout is often split into several ways, one per segment between
    entries and exits. Ways that share a node belong to the same ring, so
    grouping them by connectivity counts each roundabout exactly once. A
    distance threshold would also merge two separate roundabouts that are
    close together, and could split a very large one.
    """
    ways = [e for e in elements if e.get("type") == "way" and e.get("nodes")]
    parent = list(range(len(ways)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_way_of_node = {}
    for i, way in enumerate(ways):
        for node in way["nodes"]:
            j = first_way_of_node.setdefault(node, i)
            if j != i:
                parent[find(i)] = find(j)

    groups = {}
    for i, way in enumerate(ways):
        groups.setdefault(find(i), []).append(way)

    rows = []
    for members in groups.values():
        # Unique nodes, so the node shared by the ends of a closed ring is counted once
        coords = {}
        for way in members:
            for node, point in zip(way["nodes"], way.get("geometry", [])):
                coords[node] = (point["lat"], point["lon"])
        if not coords:
            continue
        lats, lons = zip(*coords.values())
        rows.append({
            "osm_way_ids": sorted(w["id"] for w in members),
            "latitude": float(np.mean(lats)),
            "longitude": float(np.mean(lons)),
        })

    roundabouts = pd.DataFrame(rows, columns=["osm_way_ids", "latitude", "longitude"])
    roundabouts.insert(0, "roundabout_id", range(len(roundabouts)))
    return roundabouts


def normalize_name(name):
    """Compare names case- and accent-insensitively ('ÉVORA' == 'Évora' == 'evora')."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


def region_of(latitude, longitude):
    for region, (min_lon, min_lat, max_lon, max_lat) in REGION_BOXES.items():
        if min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat:
            return region
    return "Continente"


def load_boundaries(sources):
    """Read and stack boundary layers given as (path, layer) pairs, in EPSG:4326.

    CAOP ships the mainland, Madeira and the Azores as separate layers, so
    several sources are usually needed to cover the whole country.
    """
    import geopandas as gpd

    frames = [gpd.read_file(path, layer=layer).to_crs("EPSG:4326") for path, layer in sources]
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")


def assign_municipalities(roundabouts, boundaries, name_column):
    """Attach the municipality containing each roundabout (point-in-polygon).

    Unlike reverse geocoding, this needs no API key, has no rate limit, gives
    the same answer on every run, and returns the municipality rather than
    the town, so the result joins directly with the municipality table.
    """
    import geopandas as gpd

    points = gpd.GeoDataFrame(
        roundabouts,
        geometry=gpd.points_from_xy(roundabouts["longitude"], roundabouts["latitude"]),
        crs="EPSG:4326",
    )
    polygons = boundaries[[name_column, "geometry"]].to_crs("EPSG:4326")
    # CAOP is split into parishes; dissolve so each municipality is one polygon
    polygons = polygons.dissolve(by=name_column).reset_index()
    joined = gpd.sjoin(points, polygons, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    result = roundabouts.copy()
    result["municipality"] = joined[name_column]
    return result


def load_municipalities(path="lista_municípios_pt.csv"):
    """Read the Wikipedia list with numbers parsed ('34 351' -> 34351, '714,69' -> 714.69)."""
    municipalities = pd.read_csv(path, sep=";", skiprows=1, dtype=str)
    municipalities = municipalities.rename(columns={
        "Município": "city",
        "Código de três letras": "city_code",
        "População em 2021": "population_2021",
        "Area em km^2": "area",
    })[["city", "city_code", "population_2021", "area"]]
    municipalities["population_2021"] = pd.to_numeric(
        municipalities["population_2021"].str.replace(r"\s+", "", regex=True))
    municipalities["area"] = pd.to_numeric(municipalities["area"].str.replace(",", "."))
    return municipalities


def match_city_codes(roundabouts, municipalities, name_column="municipality"):
    """Map each roundabout's municipality name to the 3-letter code of the list.

    Joining on the code instead of the name handles 'Lagoa (Açores)' vs
    'Lagoa (Algarve)' and 'Calheta (Açores)' vs 'Calheta (Madeira)'.
    """
    lookup = {}
    for city, code in zip(municipalities["city"], municipalities["city_code"]):
        base, _, qualifier = city.partition("(")
        lookup.setdefault(normalize_name(base), []).append((code, qualifier.strip(" )")))

    def code_for(row):
        candidates = lookup.get(normalize_name(row[name_column]), [])
        if len(candidates) == 1:
            return candidates[0][0]
        region = region_of(row["latitude"], row["longitude"])
        for code, qualifier in candidates:
            if qualifier == region or (region == "Continente" and qualifier not in REGION_BOXES):
                return code
        return None

    result = roundabouts.copy()
    result["city_code"] = result.apply(code_for, axis=1) if len(result) else []
    return result


def municipality_stats(roundabouts, municipalities):
    """Roundabouts per municipality; municipalities without any get 0, not NaN."""
    counts = roundabouts.dropna(subset=["city_code"]).groupby("city_code").size()
    stats = municipalities.copy()
    stats["roundabouts"] = stats["city_code"].map(counts).fillna(0).astype(int)
    stats["rb_per_km2"] = stats["roundabouts"] / stats["area"]
    stats["rb_per_10k_inhabitants"] = stats["roundabouts"] / stats["population_2021"] * 10_000
    return stats


def parse_source(source):
    """'CAOP.gpkg@cont_municipios' -> ('CAOP.gpkg', 'cont_municipios'); no '@' -> default layer."""
    path, separator, layer = source.rpartition("@")
    return (path, layer) if separator else (source, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--boundaries", required=True, action="append", metavar="PATH[@LAYER]",
                        help="municipality or parish polygons, e.g. CAOP from dgterritorio.gov.pt; "
                             "repeat for each region/layer")
    parser.add_argument("--name-column", default="municipio")
    parser.add_argument("--municipalities", default="lista_municípios_pt.csv")
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()

    roundabouts = merge_roundabout_ways(fetch_roundabout_ways())
    print(f"{len(roundabouts)} roundabouts in Portugal")

    boundaries = load_boundaries([parse_source(source) for source in args.boundaries])
    municipalities = load_municipalities(args.municipalities)
    roundabouts = assign_municipalities(roundabouts, boundaries, args.name_column)
    roundabouts = match_city_codes(roundabouts, municipalities)

    unmatched = roundabouts["city_code"].isna()
    if unmatched.any():
        print(f"Warning: {unmatched.sum()} roundabouts without a municipality: "
              f"{sorted(roundabouts.loc[unmatched, 'municipality'].dropna().unique())[:20]}")

    stats = municipality_stats(roundabouts, municipalities)
    roundabouts.to_csv(f"{args.out_dir}/Portugal_roundabouts.csv", index=False)
    stats.to_csv(f"{args.out_dir}/pt_cities_rb.csv", index=False)
    print(stats.sort_values("roundabouts", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()

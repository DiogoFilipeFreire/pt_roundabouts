import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from roundabout import (assign_municipalities, load_municipalities, match_city_codes,
                        merge_roundabout_ways, municipality_stats, normalize_name)


def way(way_id, nodes, coords, highway="primary"):
    return {"type": "way", "id": way_id, "nodes": nodes, "tags": {"highway": highway},
            "geometry": [{"lat": lat, "lon": lon} for lat, lon in coords]}


def test_ways_sharing_nodes_are_one_roundabout():
    # One ring split in two halves (share nodes 1 and 3), plus a separate ring 30 m away
    elements = [
        way(10, [1, 2, 3], [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]),
        way(11, [3, 4, 1], [(1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]),
        way(20, [5, 6, 7, 5], [(2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (2.0, 2.0)]),
    ]
    result = merge_roundabout_ways(elements)
    assert len(result) == 2
    first = result[result["osm_way_ids"].apply(lambda ids: ids == [10, 11])].iloc[0]
    assert (first["latitude"], first["longitude"]) == (0.5, 0.5)
    closed = result[result["osm_way_ids"].apply(lambda ids: ids == [20])].iloc[0]
    # Repeated closing node must not bias the centre
    assert abs(closed["latitude"] - 7 / 3) < 1e-9


def test_merge_is_transitive():
    elements = [way(1, [1, 2], [(0, 0), (0, 1)]), way(2, [3, 4], [(1, 1), (1, 0)]),
                way(3, [2, 3], [(0, 1), (1, 1)])]
    assert len(merge_roundabout_ways(elements)) == 1


def test_roundabouts_not_open_to_traffic_are_skipped():
    elements = [way(1, [1, 2], [(0, 0), (0, 1)]), way(2, [3, 4], [(1, 1), (1, 0)], highway="proposed"),
                way(3, [5, 6], [(2, 2), (2, 3)], highway="cycleway"), way(4, [7, 8], [(3, 3), (3, 4)], highway=None)]
    assert merge_roundabout_ways(elements)["osm_way_ids"].tolist() == [[1]]


def test_normalize_name():
    assert normalize_name("ÉVORA") == normalize_name("Évora") == "evora"
    assert normalize_name("Vila  Nova de Gaia ") == "vila nova de gaia"


def test_load_municipalities_parses_numbers():
    municipalities = load_municipalities()
    assert len(municipalities) == 308
    abrantes = municipalities.set_index("city").loc["Abrantes"]
    assert abrantes["population_2021"] == 34351
    assert abrantes["area"] == 714.69
    assert municipalities["population_2021"].notna().all()
    assert municipalities["area"].notna().all()
    assert municipalities["city"].str.match(r"\w").all()


def test_homonymous_municipalities_are_disambiguated():
    municipalities = load_municipalities()
    roundabouts = pd.DataFrame({
        "municipality": ["Lagoa", "LAGOA", "Calheta", "Calheta de São Jorge", "Lisboa",
                         "Paços de Ferreira", "Nowhere"],
        "latitude": [37.13, 37.75, 32.72, 38.6, 38.72, 41.27, 38.0],
        "longitude": [-8.45, -25.57, -17.18, -28.0, -9.14, -8.38, -8.0],
    })
    codes = match_city_codes(roundabouts, municipalities)["city_code"]
    assert codes.iloc[:6].tolist() == ["LGA", "LAG", "CLT", "CHT", "LSB", "PFR"]
    assert pd.isna(codes.iloc[6])


def test_stats_fill_missing_with_zero():
    municipalities = pd.DataFrame({"city": ["A", "B"], "city_code": ["AAA", "BBB"],
                                   "population_2021": [20_000, 5_000], "area": [10.0, 50.0]})
    roundabouts = pd.DataFrame({"city_code": ["AAA", "AAA", None]})
    stats = municipality_stats(roundabouts, municipalities).set_index("city_code")
    assert stats.loc["AAA", "roundabouts"] == 2
    assert stats.loc["BBB", "roundabouts"] == 0
    assert stats.loc["AAA", "rb_per_10k_inhabitants"] == 1.0
    assert stats.loc["BBB", "rb_per_km2"] == 0.0


def test_assign_municipalities_point_in_polygon():
    # Two parishes of the same municipality plus a neighbour
    boundaries = gpd.GeoDataFrame(
        {"municipio": ["Lisboa", "Lisboa", "Amadora"]},
        geometry=[box(-9.2, 38.7, -9.1, 38.8), box(-9.1, 38.7, -9.0, 38.8), box(-9.3, 38.7, -9.2, 38.8)],
        crs="EPSG:4326",
    )
    roundabouts = pd.DataFrame({"latitude": [38.75, 38.75, 38.75, 40.0],
                                "longitude": [-9.15, -9.05, -9.25, -8.0]})
    result = assign_municipalities(roundabouts, boundaries, "municipio")
    assert result["municipality"].tolist()[:3] == ["Lisboa", "Lisboa", "Amadora"]
    assert pd.isna(result["municipality"].iloc[3])


def test_homonymous_polygons_are_not_dissolved_together():
    boundaries = gpd.GeoDataFrame(
        {"dtmn": ["0808", "4201"], "municipio": ["Lagoa", "Lagoa"]},
        geometry=[box(-8.55, 37.05, -8.35, 37.2), box(-25.65, 37.7, -25.5, 37.8)],
        crs="EPSG:4326",
    )
    roundabouts = pd.DataFrame({"latitude": [37.13, 37.75], "longitude": [-8.45, -25.57]})
    result = assign_municipalities(roundabouts, boundaries, "municipio", id_column="dtmn")
    assert result["municipality"].tolist() == ["Lagoa", "Lagoa"]
    assert match_city_codes(result, load_municipalities())["city_code"].tolist() == ["LGA", "LAG"]

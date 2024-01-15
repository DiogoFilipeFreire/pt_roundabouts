import requests
import pandas as pd

def get_roundabouts_overpass(place_name):
    overpass_url = "http://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json];
    area[name="{place_name}"]->.searchArea;
    (
      way["junction"="roundabout"](area.searchArea);
    );
    out center;
    """

    response = requests.get(overpass_url, params={'data': overpass_query})
    data = response.json()

    roundabouts = []
    for element in data['elements']:
        if element['type'] == 'way' and 'center' in element:
            roundabouts.append({
                'Identifier': element['id'],
                'Latitude': element['center']['lat'],
                'Longitude': element['center']['lon'],
                'Country Code': place_name
            })

    return pd.DataFrame(roundabouts)

roundabouts_df = get_roundabouts_overpass('Portugal')
print(roundabouts_df.head(50))
print(len(roundabouts_df))
roundabouts_df.to_csv('rotundas_pt_1.csv')
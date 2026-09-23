from app.ingestion.overpass import build_overpass_query, normalize_overpass_response


def test_overpass_query_builds_around_query():
    query = build_overpass_query(12.9716, 77.5946, radius_km=2.0)
    assert 'around:2000' in query
    assert 'tourism' in query
    assert 'historic' in query


def test_overpass_response_normalization_keeps_lat_lon_and_tags():
    payload = {
        'elements': [
            {
                'id': 1,
                'lat': 12.9716,
                'lon': 77.5946,
                'tags': {'name': 'Lalbagh', 'tourism': 'park'}
            }
        ]
    }

    result = normalize_overpass_response(payload)
    assert result[0]['name'] == 'Lalbagh'
    assert result[0]['category'] == 'park'
    assert result[0]['lat'] == 12.9716

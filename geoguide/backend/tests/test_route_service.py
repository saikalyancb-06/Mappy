from app.services.route_service import RouteService


def test_route_service_estimates_distance_and_time():
    service = RouteService()
    result = service.estimate_travel({'lat': 12.9716, 'lon': 77.5946}, {'lat': 12.9800, 'lon': 77.6000}, mode='walk')
    assert result['mode'] == 'walk'
    assert result['distance_km'] > 0
    assert result['duration_minutes'] >= 5
    assert result['estimate'] is True

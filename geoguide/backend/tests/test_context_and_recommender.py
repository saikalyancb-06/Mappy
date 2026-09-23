from app.services.context_manager import ContextManager
from app.services.recommender import Recommender


def test_context_manager_builds_profile_context():
    location = {'lat': 12.9716, 'lon': 77.5946, 'city': 'Bengaluru'}
    context = ContextManager().build_context(location=location)
    assert context['location']['city'] == 'Bengaluru'
    assert context['user_profile']['language'] == 'en'
    assert 'area' in context


def test_recommender_orders_places_by_profile_fit():
    profile = {'interests': {'nature': 5, 'history': 3}, 'budget': 'moderate', 'pace': 'balanced'}
    places = [
        {'id': 'p1', 'name': 'Market street', 'category': 'shopping', 'distance_km': 4.5},
        {'id': 'p2', 'name': 'Garden park', 'category': 'nature', 'distance_km': 1.2},
        {'id': 'p3', 'name': 'Heritage museum', 'category': 'heritage', 'distance_km': 1.7},
    ]

    ranked = Recommender().rank_places(profile, places)
    assert ranked[0]['id'] == 'p2'
    assert 'Nature-focused choice' in ranked[0]['reasons']

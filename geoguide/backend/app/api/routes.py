from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.models import Area, InteractionEvent, Poi, User, UserPreference
from app.db.session import SessionLocal
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.geocode import forward_geocode
from app.services.agent import GeoGuideAgent
from app.services.auth import auth_config, hash_password, issue_token, verify_password, verify_token
from app.services.context_manager import ContextManager
from app.services.live_context import get_live_context, safe_location
from app.services.recommender import Recommender
from app.services.route_service import RouteService
from app.services.web_search import SearchProviderError, SerpApiSearchProvider, select_engine
from app.config import GROQ_API_KEY, SERPAPI_KEY
from app.services.knowledge import KnowledgeService
from app.services.location_context import LocationContextError, extract_explicit_destination, validate_location_context

router = APIRouter(prefix='/api')
context_manager = ContextManager()
agent = GeoGuideAgent()
recommender = Recommender()
route_service = RouteService()
pipeline = IngestionPipeline()
web_search = SerpApiSearchProvider()
knowledge = KnowledgeService()


def _requested_location(lat: float | None, lon: float | None, city: str | None, *, source: str = 'device', timestamp: str | None = None, accuracy_meters: float | None = None) -> dict:
    if lat is not None and lon is not None:
        try:
            context = validate_location_context({'lat': lat, 'lon': lon, 'city': city, 'source': source, 'timestamp': timestamp, 'accuracy_meters': accuracy_meters})
        except LocationContextError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {'lat': context['latitude'], 'lon': context['longitude'], 'city': city, 'location_context': context}
    if city:
        resolved = forward_geocode(city)
        if not resolved or resolved.get('lat') is None or resolved.get('lon') is None:
            raise HTTPException(status_code=422, detail='The destination could not be resolved.')
        context = {'latitude': float(resolved['lat']), 'longitude': float(resolved['lon']), 'accuracy_meters': None, 'source': 'resolved_place', 'timestamp': datetime.now(timezone.utc).isoformat(), 'confidence': 0.8, 'city': resolved.get('city') or city}
        return {'lat': context['latitude'], 'lon': context['longitude'], 'city': context['city'], 'location_context': context}
    raise HTTPException(status_code=422, detail='A current location or explicit destination is required.')


def _query_location(question: str, payload: dict[str, Any]) -> tuple[dict, dict | None]:
    physical = payload.get('location_context') or payload.get('location')
    explicit = payload.get('query_destination') or extract_explicit_destination(question)
    if explicit:
        destination = forward_geocode(str(explicit))
        if not destination or destination.get('lat') is None or destination.get('lon') is None:
            raise HTTPException(status_code=422, detail='The requested destination could not be resolved.')
        context = {'latitude': float(destination['lat']), 'longitude': float(destination['lon']), 'accuracy_meters': None, 'source': 'user_explicit_query', 'timestamp': datetime.now(timezone.utc).isoformat(), 'confidence': 0.8, 'city': destination.get('city') or explicit}
        return {'lat': context['latitude'], 'lon': context['longitude'], 'city': context['city'], 'location_context': context}, context
    if not physical:
        raise HTTPException(status_code=422, detail='A current location or active destination is required.')
    try:
        context = validate_location_context(physical)
    except LocationContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {'lat': context['latitude'], 'lon': context['longitude'], 'city': context.get('city'), 'location_context': context}, None


def _auth_user(authorization: str | None) -> User:
    token = authorization.removeprefix('Bearer ').strip() if authorization else None
    user_id = verify_token(token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired session.')
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Account not found.')
        return user


def _preference_payload(preference) -> dict:
    return {
        'interests': json.loads(preference.interests or '{}'),
        'budget': preference.budget,
        'pace': preference.pace,
        'likes': json.loads(preference.likes or '[]'),
        'dislikes': json.loads(preference.dislikes or '[]'),
        'updated_at': preference.updated_at.isoformat() if preference.updated_at else None,
    }


def _stored_places(location: dict, radius_km: float = 5.0) -> list[dict]:
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / max(111.0 * abs(__import__('math').cos(__import__('math').radians(location['lat']))), 0.01)
    with SessionLocal() as db:
        area = db.query(Area).filter(
            Area.lat.between(location['lat'] - lat_delta, location['lat'] + lat_delta),
            Area.lon.between(location['lon'] - lon_delta, location['lon'] + lon_delta),
        ).order_by(Area.created_at.desc()).first()
        if not area:
            return []
        places = []
        for poi in db.query(Poi).filter(Poi.area_id == area.id).all():
            distance = route_service.haversine_km(location['lat'], location['lon'], poi.lat, poi.lon)
            if distance <= radius_km:
                places.append({'id': poi.id, 'name': poi.name, 'category': poi.category or 'place', 'lat': poi.lat, 'lon': poi.lon, 'distance_km': round(distance, 2), 'source': poi.source or 'structured store', 'opening_hours': poi.opening_hours, 'open_now': poi.open_now if poi.opening_hours else None})
        return places


def _deduplicate_evidence(items: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in items:
        identity = str(item.get('name') or item.get('title') or item.get('source_url') or item.get('url') or item.get('id') or '').casefold()
        identity = re.sub(r'[^a-z0-9]+', ' ', identity).strip()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


@router.post('/auth/signup')
def signup(payload: dict | None) -> dict:
    safe = payload or {}
    email = str(safe.get('email', '')).strip().lower()
    password = str(safe.get('password', ''))
    name = str(safe.get('name', '')).strip() or 'Local explorer'
    if '@' not in email or len(password) < 6:
        raise HTTPException(status_code=400, detail='Use a valid email and a password with at least 6 characters.')
    user = User(id=__import__('uuid').uuid4().hex, email=email, name=name, password_hash=hash_password(password))
    try:
        with SessionLocal() as db:
            db.add(user)
            db.commit()
    except IntegrityError:
        raise HTTPException(status_code=409, detail='An account with this email already exists.') from None
    return {'token': issue_token(user.id), 'user': {'id': user.id, 'email': user.email, 'name': user.name}, 'auth': auth_config()}


@router.post('/auth/login')
def login(payload: dict | None) -> dict:
    safe = payload or {}
    email = str(safe.get('email', '')).strip().lower()
    password = str(safe.get('password', ''))
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail='Invalid email or password.')
        return {'token': issue_token(user.id), 'user': {'id': user.id, 'email': user.email, 'name': user.name}, 'auth': auth_config()}


@router.get('/auth/me')
def current_user(authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    return {'user': {'id': user.id, 'email': user.email, 'name': user.name}}


@router.post('/auth/logout')
def logout(authorization: str | None = Header(default=None)) -> dict:
    _auth_user(authorization)
    return {'status': 'ok'}


@router.get('/preferences')
def get_preferences(authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    with SessionLocal() as db:
        preference = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        if preference is None:
            preference = UserPreference(id=uuid.uuid4().hex, user_id=user.id)
            db.add(preference)
            db.commit()
        return _preference_payload(preference)


@router.put('/preferences')
def update_preferences(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    safe = payload or {}
    with SessionLocal() as db:
        preference = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        if preference is None:
            preference = UserPreference(id=uuid.uuid4().hex, user_id=user.id)
            db.add(preference)
        for key in ('budget', 'pace'):
            if isinstance(safe.get(key), str) and safe[key].strip():
                setattr(preference, key, safe[key].strip())
        for key in ('interests', 'likes', 'dislikes'):
            if isinstance(safe.get(key), (dict, list)):
                setattr(preference, key, json.dumps(safe[key]))
        db.commit()
        return _preference_payload(preference)


@router.post('/interactions')
def record_interaction(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    safe = payload or {}
    event_type = str(safe.get('event_type') or '').strip()
    if event_type not in {'clicked', 'saved', 'unsaved', 'dismissed', 'feedback'}:
        raise HTTPException(status_code=422, detail='Unsupported interaction type.')
    with SessionLocal() as db:
        db.add(InteractionEvent(id=uuid.uuid4().hex, user_id=user.id, poi_id=str(safe.get('poi_id') or '') or None, event_type=event_type))
        category = str(safe.get('category') or '').strip().casefold()
        if category and event_type in {'saved', 'unsaved'}:
            preference = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
            if preference is None:
                preference = UserPreference(id=uuid.uuid4().hex, user_id=user.id)
                db.add(preference)
            interests = json.loads(preference.interests or '{}')
            delta = 0.25 if event_type == 'saved' else -0.1
            interests[category] = round(max(0.0, min(5.0, float(interests.get(category, 0)) + delta)), 2)
            preference.interests = json.dumps(interests)
        db.commit()
    return {'status': 'recorded'}


@router.get('/health')
def health() -> dict:
    return {'status': 'ok', 'services': {'sqlite': 'ready', 'qdrant': 'ready' if knowledge.store.client is not None else 'degraded', 'groq': 'configured' if GROQ_API_KEY else 'unconfigured', 'serpapi': 'configured' if SERPAPI_KEY else 'unconfigured'}}


@router.post('/location')
def location(payload: dict | None) -> dict:
    safe_payload = payload or {}
    try:
        lat = float(safe_payload['lat']) if safe_payload.get('lat') is not None else None
        lon = float(safe_payload['lon']) if safe_payload.get('lon') is not None else None
    except (TypeError, ValueError):
        lat, lon = None, None
    city = str(safe_payload.get('city') or '').strip() or None
    resolved = None
    if city and (lat is None or lon is None):
        resolved = forward_geocode(city)
        if not resolved or resolved.get('lat') is None or resolved.get('lon') is None:
            raise HTTPException(status_code=422, detail='The destination could not be resolved.')
        lat, lon = float(resolved['lat']), float(resolved['lon'])
        city = resolved.get('city') or city
    context = context_manager.build_context(location={'lat': lat, 'lon': lon, 'city': city})
    job = pipeline.start(lat, lon, safe_payload.get('accuracy'), city=city)
    return {
        'status': 'queued',
        'job_id': job.job_id,
        'area_id': context['area']['id'],
        'location': {'lat': lat, 'lon': lon, 'city': city},
        'step': job.step,
        'progress': job.progress,
        'message': 'Location ingestion started.',
    }


@router.get('/ingestion/{job_id}/events')
def ingestion_events(job_id: str) -> dict:
    return pipeline.status(job_id)


@router.get('/now')
def now(lat: float | None = None, lon: float | None = None, city: str | None = None, source: str = 'device', timestamp: str | None = None, accuracy_meters: float | None = None) -> dict:
    location = _requested_location(lat, lon, city, source=source, timestamp=timestamp, accuracy_meters=accuracy_meters)
    live = get_live_context(location['lat'], location['lon'], location.get('city'), include_places=False)
    context = context_manager.build_context(location=live['location'])
    return {
        'location': context['location'],
        'area': live['area'],
        'local_time': context['local_time'],
        'weather': live['weather'],
        'daylight_left': 'unknown',
        'advisories': [],
        'briefing': 'Live place, weather, and map context is being used for this area.' if not live['provider_errors'] else 'Some live context providers are unavailable; showing the data that could be retrieved.',
        'data_status': 'live' if not live['provider_errors'] else 'partial',
        'provider_errors': live['provider_errors'],
        'location_context': location['location_context'],
    }



_MODE_RADIUS: dict[str, float] = {'nearby': 2.0, 'now': 2.0, 'everywhere': 10.0}


@router.get('/nearby')
def nearby(lat: float | None = None, lon: float | None = None, city: str | None = None, radius_km: float = 2.0, mode: str = 'nearby', category: str | None = None, source: str = 'device', timestamp: str | None = None, accuracy_meters: float | None = None, authorization: str | None = Header(default=None)) -> dict:
    request_id = uuid.uuid4().hex
    location = _requested_location(lat, lon, city, source=source, timestamp=timestamp, accuracy_meters=accuracy_meters)
    # Each mode has its own geographic scope independent of the generic radius_km parameter
    if mode == 'everywhere':
        safe_radius = 10.0
    elif mode == 'now':
        safe_radius = min(max(float(radius_km), 0.5), _MODE_RADIUS.get('now', 2.0))
    else:
        safe_radius = min(max(float(radius_km), 0.5), _MODE_RADIUS.get('nearby', 2.0))

    persisted = _stored_places(location, safe_radius)
    live = get_live_context(location['lat'], location['lon'], location.get('city'), radius_km=safe_radius) if not persisted else {'location': location, 'places': [], 'provider_errors': []}
    places = list(persisted or live.get('places', []))

    # If neither database nor Overpass returned places, use live web search (SerpApi) as robust fallback
    if not places and SERPAPI_KEY:
        try:
            category_query = category or 'cafes restaurants parks attractions scenic places'
            search_query = f'{category_query} near me'
            web_resp = web_search.search(search_query, location, limit=15)
            # Allow up to 10.0km for web fallback if category is attractions/hotels or mode is everywhere
            filter_radius = 10.0 if (mode == 'everywhere' or category in {'attractions', 'hotels'}) else max(safe_radius, 4.0)
            filtered_web = web_search.filter_by_distance(list(web_resp.results), location['lat'], location['lon'], filter_radius)
            if not filtered_web:
                # If distance filter was too tight, still keep the closest web candidates rather than returning nothing
                filtered_web = list(web_resp.results)[:8]
            for item in filtered_web:
                cat_name = category or ('cafe' if 'cafe' in item.title.lower() else 'restaurant' if 'restaurant' in item.title.lower() or 'kitchen' in item.title.lower() else 'hotel' if 'hotel' in item.title.lower() else 'attraction')
                places.append({
                    'id': f'web-{uuid.uuid5(uuid.NAMESPACE_URL, item.url or item.title).hex[:12]}',
                    'name': item.title,
                    'category': cat_name,
                    'lat': item.latitude or location['lat'],
                    'lon': item.longitude or location['lon'],
                    'address': item.address,
                    'rating': item.rating,
                    'opening_hours': item.opening_hours,
                    'source': 'Live Search (SerpApi)',
                })
        except Exception as exc:
            logger.info('nearby_web_fallback_failed: %s', exc)

    # Filter by requested category tab if specified (e.g. 'hotels', 'attractions')
    if category:
        cat_lower = category.lower()
        if cat_lower == 'hotels':
            hotel_keywords = {'hotel', 'resort', 'inn', 'lodge', 'stay', 'guest', 'hostel'}
            places = [p for p in places if any(kw in str(p.get('name', '')).lower() or kw in str(p.get('category', '')).lower() for kw in hotel_keywords)]
        elif cat_lower == 'attractions':
            attraction_keywords = {'park', 'garden', 'museum', 'monument', 'temple', 'historic', 'tourism', 'viewpoint', 'lake', 'art', 'palace', 'heritage'}
            places = [p for p in places if any(kw in str(p.get('name', '')).lower() or kw in str(p.get('category', '')).lower() for kw in attraction_keywords)]

    # Deterministic mode-specific filters — no LLM involvement
    if mode == 'now':
        # 'Now' semantics: only places that have verifiable open state or known opening hours
        places = [place for place in places if place.get('open_now') is True or place.get('opening_hours')]
    elif mode == 'everywhere':
        # 'Everywhere' semantics: broader discovery, prefer highly-rated over strictly nearest
        places.sort(key=lambda p: (-float(p.get('rating') or 0), float(p.get('distance_km') or 0)))

    # Recalculate distance deterministically from the validated user coordinates for all modes
    for place in places:
        place['distance_km'] = round(route_service.haversine_km(location['lat'], location['lon'], float(place['lat']), float(place['lon'])), 2)

    if mode == 'nearby':
        # 'Nearby' semantics: strict proximity ordering
        places.sort(key=lambda p: p['distance_km'])

    profile = {'budget': 'moderate', 'pace': 'balanced'}
    if authorization and isinstance(authorization, str) and authorization.strip():
        try:
            user = _auth_user(authorization)
            with SessionLocal() as db:
                preference = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
                if preference:
                    profile.update({'budget': preference.budget, 'pace': preference.pace, 'interests': json.loads(preference.interests or '{}')})
        except Exception:
            pass

    ranked = recommender.rank_places(profile, places)
    return {
        'items': ranked,
        'location': live['location'],
        'location_context': location['location_context'],
        'mode': mode,
        'radius_km': safe_radius,
        'data_status': 'live' if not live['provider_errors'] else 'partial',
        'provider_errors': live['provider_errors'],
        'diagnostics': {
            'request_id': request_id,
            'location_source': location['location_context']['source'],
            'retrieval_mode': mode,
            'search_radius_km': safe_radius,
            'internal_candidate_count': len(persisted),
            'live_candidate_count': len(live.get('places', [])),
            'final_candidate_count': len(ranked),
        },
    }



@router.post('/ask')
def ask(payload: dict | None) -> dict:
    safe_payload = payload or {}
    request_id = uuid.uuid4().hex
    question = str(safe_payload.get('question') or '').strip()
    if not question:
        raise HTTPException(status_code=422, detail='A question is required.')
    location, query_destination = _query_location(question, safe_payload)
    structured = _stored_places(location, 5.0)
    live = {'location': location, 'places': [], 'provider_errors': []}
    if not structured:
        live = get_live_context(location['lat'], location['lon'], location.get('city'), radius_km=5.0)
    evidence = (structured or live.get('places', []))[:10]
    with SessionLocal() as db:
        area = db.query(Area).filter(Area.lat.between(location['lat'] - 0.15, location['lat'] + 0.15), Area.lon.between(location['lon'] - 0.15, location['lon'] + 0.15)).order_by(Area.created_at.desc()).first()
    rag_evidence = knowledge.retrieve(question, area.id if area else None, limit=5)
    evidence.extend({'name': item.get('entity_id') or item.get('text', 'Knowledge result'), 'category': item.get('category') or 'city knowledge', 'source': item.get('source', 'RAG'), 'description': item.get('text', '')} for item in rag_evidence if item.get('text'))
    web_results = []
    web_error = None
    intent_engine = select_engine(question)
    requires_live = intent_engine != 'google_maps' or len(evidence) < 3 or bool(re.search(r'\b(now|today|tonight|current|latest|open|good|recommend|cafe|restaurant|park|hotel|attraction)\b', question.casefold()))
    if requires_live:
        try:
            web_response = web_search.search(question, location, freshness='day' if requires_live else None, limit=8)
            live_candidates = list(web_response.results)
            if intent_engine == 'google_maps':
                filtered_candidates = web_search.filter_by_distance(live_candidates, location['lat'], location['lon'], 10.0)
                if filtered_candidates:
                    live_candidates = filtered_candidates
            web_results = [web_search.extract(result).as_dict() for result in live_candidates[:6]]
            # Add web results at the beginning of evidence so top local places are prioritized by the agent
            web_evidence = [{
                'name': item['title'],
                'category': 'local place',
                'source_url': item['url'],
                'source': item['source'],
                'description': item['snippet'],
                'content': item.get('content'),
                'address': item.get('address'),
                'rating': item.get('rating'),
                'opening_hours': item.get('opening_hours'),
                'lat': item.get('latitude') or item.get('lat'),
                'lon': item.get('longitude') or item.get('lon'),
                'distance_km': round(route_service.haversine_km(location['lat'], location['lon'], float(item.get('latitude') or item.get('lat')), float(item.get('longitude') or item.get('lon'))), 2) if (item.get('latitude') or item.get('lat')) and (item.get('longitude') or item.get('lon')) else None
            } for item in web_results]
            evidence = web_evidence + evidence
        except SearchProviderError as exc:
            web_error = {'code': exc.code, 'message': exc.message}
        except Exception as exc:
            web_error = {'code': 'search_error', 'message': str(exc)}
    evidence = _deduplicate_evidence(evidence)
    # Calculate distance_km for any evidence items that have lat/lon
    for item in evidence:
        if item.get('distance_km') is None and item.get('lat') and item.get('lon'):
            try:
                item['distance_km'] = round(route_service.haversine_km(location['lat'], location['lon'], float(item['lat']), float(item['lon'])), 2)
            except Exception:
                pass
    context = context_manager.build_context(location=live['location'])
    answer = agent.answer(question, context, evidence=evidence)
    answer['results'] = evidence[:10]
    answer['diagnostics'] = {
        'request_id': request_id,
        'intent_engine': intent_engine,
        'structured_candidates_count': len(structured),
        'web_search_performed': requires_live,
        'web_results_count': len(web_results),
        'web_error': web_error,
        'provider_errors': live.get('provider_errors', []),
        'location_context': location['location_context'],
        'query_destination': query_destination,
        'retrieval_mode': 'local' if intent_engine == 'google_maps' else 'hybrid',
        'search_radius_km': 5.0,
        'internal_candidate_count': len(structured),
        'filtered_candidate_count': len(evidence),
        'final_candidate_count': len(answer.get('results', [])),
    }
    return answer

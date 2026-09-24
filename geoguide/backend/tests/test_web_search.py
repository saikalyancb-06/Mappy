import httpx
import pytest

from app.services.web_search import SearchProviderError, SearchResult, SerpApiSearchProvider, select_engine


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError('failed', request=httpx.Request('GET', 'https://serpapi.com'), response=httpx.Response(self.status_code))

    def json(self):
        return self.payload


class FakeClient:
    response = FakeResponse({'organic_results': [{'title': 'Local guide', 'link': 'https://example.com/place', 'snippet': 'Useful local information.'}]})

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        self.params = params
        return self.response


def test_engine_selection_is_generic():
    assert select_engine('latest local events') == 'google_news'
    assert select_engine('photography ideas') == 'google_images'
    assert select_engine('cafes near me') == 'google_maps'
    assert select_engine('how does local history work') == 'google'


def test_missing_api_key_is_explicit():
    with pytest.raises(SearchProviderError) as error:
        SerpApiSearchProvider(api_key='').search('fresh local information')
    assert error.value.code == 'web_search_unavailable'


def test_google_results_are_normalized_and_urls_preserved(monkeypatch):
    monkeypatch.setattr('app.services.web_search.httpx.Client', FakeClient)
    response = SerpApiSearchProvider(api_key='test-key').search('fresh local information')
    assert response.engine == 'google'
    assert response.results[0].url == 'https://example.com/place'
    assert response.results[0].source == 'example.com'


def test_maps_news_and_images_use_correct_result_keys(monkeypatch):
    monkeypatch.setattr('app.services.web_search.httpx.Client', FakeClient)
    for query, engine, key in (
        ('cafes near me', 'google_maps', 'local_results'),
        ('latest events', 'google_news', 'news_results'),
        ('photography ideas', 'google_images', 'images_results'),
    ):
        FakeClient.response = FakeResponse({key: [{'title': 'Result', 'link': 'https://example.com/result'}]})
        response = SerpApiSearchProvider(api_key='test-key').search(query)
        assert response.engine == engine
        assert response.results[0].url.endswith('/result')


def test_empty_and_malformed_results_are_safe(monkeypatch):
    monkeypatch.setattr('app.services.web_search.httpx.Client', FakeClient)
    FakeClient.response = FakeResponse({'organic_results': []})
    assert SerpApiSearchProvider(api_key='test-key').search('anything').results == ()
    FakeClient.response = FakeResponse({'organic_results': [{'title': 'Missing URL'}]})
    assert SerpApiSearchProvider(api_key='test-key').search('anything else').results == ()


def test_provider_failure_and_timeout_are_controlled(monkeypatch):
    class FailingClient(FakeClient):
        def get(self, url, params=None):
            raise httpx.TimeoutException('slow')

    monkeypatch.setattr('app.services.web_search.httpx.Client', FailingClient)
    with pytest.raises(SearchProviderError) as error:
        SerpApiSearchProvider(api_key='test-key').search('fresh information')
    assert error.value.code == 'timeout'


def test_page_extraction_removes_hidden_boilerplate(monkeypatch):
    class HtmlClient(FakeClient):
        def get(self, url, params=None):
            return FakeResponse(type('Payload', (), {})(), 200)

    class HtmlResponse:
        status_code = 200
        text = '<html><nav>Menu</nav><main>Useful local facts</main><script>secret()</script></html>'

        def raise_for_status(self):
            return None

    class HtmlClient2(FakeClient):
        def get(self, url, params=None):
            return HtmlResponse()

    monkeypatch.setattr('app.services.web_search.httpx.Client', HtmlClient2)
    provider = SerpApiSearchProvider(api_key='test-key')
    result = provider.extract(SearchResult('Place', 'https://example.com/place', '', 'example.com'))
    assert result.content == 'Useful local facts'

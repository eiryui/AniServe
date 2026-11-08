import pytest

COLLECTIONS = ["/trending", "/popular", "/recent", "/upcoming", "/schedule"]
SLIM_KEYS = {"id", "title", "coverImage", "studios", "format", "status", "genres", "tags", "isAdult", "startDate", "endDate"}


def get(client, path, **params):
    return client.get(path, params=params)


def test_health(client):
    body = get(client, "/health").json()
    assert body == {"status": "online", "environment": "production", "database": "connected"}


@pytest.mark.parametrize("path", COLLECTIONS)
def test_collection_envelope_and_paging(client, path):
    r = get(client, path, per_page=3)
    assert r.status_code == 200
    body = r.json()
    assert list(body) == ["page", "perPage", "total", "hasNextPage", "results"]
    assert body["page"] == 1 and body["perPage"] == 3 and len(body["results"]) <= 3
    assert body["hasNextPage"] == (body["total"] > 3)
    for item in body["results"]:
        assert SLIM_KEYS <= set(item)
        assert "description" not in item and "characters" not in item and "relations" not in item
        assert item["isAdult"] is False


@pytest.mark.parametrize("path", COLLECTIONS)
@pytest.mark.parametrize("params", [{"per_page": 0}, {"per_page": 51}, {"page": 0}])
def test_collection_validation(client, path, params):
    assert get(client, path, **params).status_code == 422


def test_pages_do_not_overlap(client):
    a = get(client, "/popular", per_page=10, page=1).json()["results"]
    b = get(client, "/popular", per_page=10, page=2).json()["results"]
    assert not {x["id"] for x in a} & {x["id"] for x in b}


def test_sorting(client):
    r = get(client, "/filter", sort="POPULARITY_DESC", per_page=50, isAdult="true").json()["results"]
    pops = [x["popularity"] for x in r if x["popularity"] is not None]
    assert pops == sorted(pops, reverse=True)
    r = get(client, "/filter", sort="ID_ASC", per_page=50).json()["results"]
    assert [x["id"] for x in r] == sorted(x["id"] for x in r)
    assert get(client, "/filter", sort="NAME_DESC").status_code == 400


def test_filter_combines_with_and(client):
    body = get(client, "/filter", genre=["Action", "Comedy"], per_page=50).json()
    assert all({"Action", "Comedy"} <= set(x["genres"]) for x in body["results"])
    body = get(client, "/filter", status="releasing", format="tv", per_page=50, isAdult="true").json()
    assert all(x["status"] == "RELEASING" and x["format"] == "TV" for x in body["results"])


def test_search_matches_every_word_in_any_order(client, ids):
    # Fixture title "<Word1> <Word2> <id>": the same words reversed must still find it, and one word that is not in it must not.
    found = get(client, "/search", query=f"{ids[1]} The", isAdult="true").json()["results"]
    assert ids[1] in [x["id"] for x in found]
    assert get(client, "/search", query=f"{ids[1]} zzzznomatch", isAdult="true").json()["total"] == 0
    assert get(client, "/search", query="zzzznomatch").json()["total"] == 0
    assert get(client, "/search").status_code == 422


def test_search_treats_like_wildcards_literally(client):
    assert get(client, "/search", query="100%", isAdult="true").json()["total"] >= 1
    assert get(client, "/search", query="%", isAdult="true").json()["total"] < get(client, "/search", query="e", isAdult="true").json()["total"]


def test_suggestions_shape(client):
    body = get(client, "/suggestions", query="the", isAdult="true").json()
    assert list(body) == ["suggestions"] and len(body["suggestions"]) <= 8
    assert list(body["suggestions"][0]) == ["id", "title", "title_romaji", "poster", "format", "status", "year", "episodes"]


def test_spotlight_is_bare_results(client):
    body = get(client, "/spotlight").json()
    assert list(body) == ["results"] and len(body["results"]) <= 10


def test_schedule_items(client):
    body = get(client, "/schedule", per_page=50).json()
    assert body["total"] > 0
    airings = [x["airingAt"] for x in body["results"]]
    assert airings == sorted(airings)
    for x in body["results"]:
        assert {"next_episode", "airingAt", "timeUntilAiring"} <= set(x) and x["timeUntilAiring"] >= 0


def test_genres_and_tags(client):
    plain = get(client, "/genres").json()["results"]; adult = get(client, "/genres", isAdult="true").json()["results"]
    assert {"name": "Hentai", "isAdult": True} in adult and all(g["name"] != "Hentai" for g in plain)
    assert len(adult) == len(plain) + 1
    tags = get(client, "/tags").json()["results"]
    assert tags and all(t["isAdult"] is False for t in tags) and tags == get(client, "/tags", isAdult="true").json()["results"]


def test_list(client, ids):
    body = get(client, "/list", per_page=500, isAdult="true").json()
    assert body["total"] == len(ids) and [x["id"] for x in body["results"]] == sorted(ids)
    assert get(client, "/list", ids_only="true", per_page=500, isAdult="true").json()["results"] == sorted(ids)
    assert get(client, "/list", per_page=501).status_code == 422
    assert get(client, "/list").json()["total"] < len(ids)  # adult titles are hidden by default


def test_adult_titles_are_hidden_everywhere(client, adult_ids):
    a = adult_ids[0]
    assert get(client, f"/info/{a}").status_code == 404
    assert get(client, f"/anime/{a}/characters").status_code == 404
    assert get(client, f"/info/{a}", isAdult="true").status_code == 200
    for path in COLLECTIONS + ["/popular"]:
        assert all(not x["isAdult"] for x in get(client, path, per_page=50).json()["results"])


def test_info_shape(client, ids):
    body = get(client, f"/info/{ids[4]}", isAdult="true")  # a title with extras (every 4th id)
    assert body.status_code == 200
    info = body.json()
    for key in ("staff", "externalLinks", "streamingEpisodes", "stats", "characters", "relations", "recommendations"):
        assert key in info
    assert "updatedAt" not in info and "_extrasAt" not in info and "mainStudios" not in info
    assert len(info["recommendations"]["nodes"]) <= 10
    for edge in info["characters"]["edges"]:
        assert all(v["languageV2"] == "Japanese" for v in edge["voiceActors"])
    assert "pageInfo" not in info["characters"] and "pageInfo" not in info["recommendations"]


def test_info_without_extras_reports_partial(client, ids):
    for i in ids:
        r = get(client, f"/info/{i}", isAdult="true")
        if r.headers["x-index-status"] == "partial":
            assert r.json()["staff"] == {"edges": []} and r.json()["stats"] == {"scoreDistribution": [], "statusDistribution": []}
            return
    pytest.fail("no title without extras in the fixture")


def test_info_404(client):
    r = get(client, "/info/9999999")
    assert r.status_code == 404 and r.json() == {"detail": "Anime not found"}
    assert get(client, "/info/abc").status_code == 422


def _with_total(client, ids, kind, minimum):
    for i in ids:
        r = get(client, f"/anime/{i}/{kind}", isAdult="true", per_page=1).json()
        if r["total"] >= minimum: return i, r["total"]
    pytest.skip(f"no fixture title with {minimum}+ {kind}")


@pytest.mark.parametrize("kind", ["characters", "recommendations"])
def test_detail_without_per_page_returns_everything_indexed(client, ids, kind):
    i, total = _with_total(client, ids, kind, 26)
    r = get(client, f"/anime/{i}/{kind}", isAdult="true")
    body = r.json()
    assert body["page"] == 1 and body["perPage"] == body["total"] == total and body["hasNextPage"] is False
    assert len(body[kind]) <= total
    assert r.headers["x-index-status"] in {"complete", "partial"}
    assert get(client, f"/anime/{i}/{kind}", isAdult="true", page=2).json()[kind] == []


@pytest.mark.parametrize("kind", ["characters", "recommendations"])
def test_detail_paging_is_cut_from_blocks_of_25(client, ids, kind):
    for i in ids:
        whole = get(client, f"/anime/{i}/{kind}", isAdult="true")
        if whole.headers["x-index-status"] == "complete" and whole.json()["total"] > 40: break
    else: pytest.skip("no complete title with more than 40 items")
    items = whole.json()[kind]
    first = get(client, f"/anime/{i}/{kind}", isAdult="true", per_page=30, page=1).json()
    second = get(client, f"/anime/{i}/{kind}", isAdult="true", per_page=30, page=2).json()
    assert first[kind] == items[:30] and second[kind] == items[30:60]
    assert first["hasNextPage"] is True and first["perPage"] == 30


def test_relations(client, ids):
    for i in ids:
        r = get(client, f"/anime/{i}/relations", isAdult="true").json()
        if len(r["relations"]) >= 3: break
    assert list(r) == ["id", "title", "relations"] and list(r["title"]) == ["romaji", "english"]
    paged = get(client, f"/anime/{i}/relations", isAdult="true", per_page=2).json()
    assert paged["total"] == len(r["relations"]) and paged["relations"] == r["relations"][:2] and paged["hasNextPage"] is True


def test_strict_shape_trims_nested_titles(client, ids):
    for i in ids:
        nodes = get(client, f"/anime/{i}/recommendations", isAdult="true").json()["recommendations"]
        if nodes: break
    assert set(nodes[0]["mediaRecommendation"]) <= {"id", "title", "coverImage", "bannerImage", "format", "episodes", "status", "meanScore", "averageScore", "popularity", "genres", "startDate"}
    assert "isAdult" not in nodes[0]["mediaRecommendation"]


def test_strict_shape_has_no_cover_colour_on_nested_titles(client, ids):
    for i in ids[:40]:
        for e in get(client, f"/info/{i}", isAdult="true").json()["relations"]["edges"]:
            assert set(e["node"]["coverImage"]) <= {"large"}
    assert all("color" not in x["coverImage"] for x in get(client, "/trending", per_page=5).json()["results"])


def test_aniserve_is_read_only(aniserve):
    import psycopg2
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        with aniserve.catalog.cursor() as c: c.execute("DELETE FROM anilist_catalog")
    with aniserve.catalog.cursor() as c:
        c.execute("SELECT count(*) FROM anilist_catalog"); assert c.fetchone()[0] > 0


def test_rate_limit_headers_and_429(client, aniserve):
    limiter = aniserve.api_limiter
    saved = (limiter.limit, limiter.clients)
    limiter.limit, limiter.clients = 2, {}
    try:
        first = client.get("/genres"); assert first.headers["x-ratelimit-limit"] == "2" and first.headers["x-ratelimit-remaining"] == "1"
        client.get("/genres")
        blocked = client.get("/genres")
        assert blocked.status_code == 429 and "retry-after" in blocked.headers
        assert blocked.json()["detail"] == "API rate limit exceeded. Try again after the requested window." and "retry_after" in blocked.json()
        assert client.get("/assets/landing.css").status_code == 200  # static assets are not counted
    finally:
        limiter.limit, limiter.clients = saved


def test_cors(client):
    r = client.get("/genres", headers={"Origin": "https://example.org"})
    assert r.headers["access-control-allow-origin"] in {"*", "https://example.org"}
    pre = client.options("/genres", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200


def test_landing_docs_and_static_routes(client):
    home = client.get("/")
    assert home.status_code == 200 and "AniServe" in home.text and "{{VERSION}}" not in home.text
    assert client.get("/docs").status_code == 200 and client.get("/redoc").status_code == 200 and client.get("/swagger").status_code == 200
    assert "MIT License" in client.get("/license").text
    assert client.get("/favicon.ico").headers["content-type"] == "image/png"
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "AniServe"
    for path in ["/search", "/suggestions", "/filter", "/multi-search", "/trending", "/popular", "/recent", "/upcoming", "/spotlight", "/schedule", "/genres", "/tags", "/list", "/health",
                 "/info/{anilist_id}", "/anime/{anilist_id}/characters", "/anime/{anilist_id}/relations", "/anime/{anilist_id}/recommendations"]:
        assert path in spec["paths"], path


def test_every_documented_route_exists(client):
    """endpoints.js is the landing page's reference: every example in it must be a real route that answers."""
    import re
    src = client.get("/assets/endpoints.js").text
    for example in re.findall(r'example: "([^"]+)"', src):
        assert client.get(example).status_code in {200, 404}, example  # 404 only for ids a fresh database does not have

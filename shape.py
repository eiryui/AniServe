"""Record shaping: stored catalogue records -> public JSON.

Pure functions, no database access, so every storage engine produces byte-identical output.
Derived from code by JsmaAtom (MIT). See LICENSE.
"""
import os, time

STRICT = os.getenv("API_STRICT_MIRURO_SHAPE", "true").strip().lower() in {"1", "true", "yes", "on"}

# JSONB stores object keys sorted by length, so stored records come back shuffled.
# ordered() restores one fixed, readable field order (the order Miruro's /info returns).
TOP_ORDER = ["id", "idMal", "title", "description", "coverImage", "bannerImage", "format", "season", "seasonYear", "episodes", "duration", "status", "averageScore", "meanScore", "popularity", "favourites", "trending", "genres", "tags", "source", "countryOfOrigin", "isAdult", "hashtag", "synonyms", "siteUrl", "trailer", "studios", "mainStudios", "updatedAt", "nextAiringEpisode", "startDate", "endDate", "characters", "staff", "relations", "recommendations", "externalLinks", "streamingEpisodes", "stats", "_extrasAt"]
_MEDIA_REF = ["id", "title", "coverImage", "bannerImage", "isAdult", "format", "episodes", "status", "meanScore", "averageScore", "popularity", "genres", "startDate"]
ORDER_BY_PATH = {
    "": TOP_ORDER, "studios": ["nodes"], "mainStudios": ["nodes"],
    "studios.nodes": ["id", "name", "isAnimationStudio", "siteUrl"], "mainStudios.nodes": ["name", "isAnimationStudio"],
    "tags": ["name", "rank", "isMediaSpoiler"], "relations": ["edges"], "relations.edges": ["relationType", "node"],
    "relations.edges.node": ["id", "title", "coverImage", "bannerImage", "isAdult", "format", "type", "status", "episodes", "chapters", "meanScore", "averageScore", "popularity", "startDate"],
    "recommendations": ["pageInfo", "nodes"], "recommendations.nodes": ["rating", "mediaRecommendation"], "recommendations.nodes.mediaRecommendation": _MEDIA_REF,
    "characters": ["pageInfo", "edges"], "characters.edges": ["role", "node", "voiceActors"], "characters.edges.node": ["id", "name", "image"], "characters.edges.voiceActors": ["id", "name", "image", "languageV2"],
    "staff": ["edges"], "staff.edges": ["role", "node"], "staff.edges.node": ["id", "name", "image"],
    "externalLinks": ["url", "site", "type"], "streamingEpisodes": ["title", "thumbnail", "url", "site"],
    "stats": ["scoreDistribution", "statusDistribution"], "stats.scoreDistribution": ["score", "amount"], "stats.statusDistribution": ["status", "amount"],
}
ORDER_BY_KEY = {
    "title": ["romaji", "english", "native"], "coverImage": ["large", "extraLarge", "medium", "color"], "trailer": ["id", "site", "thumbnail"],
    "nextAiringEpisode": ["episode", "airingAt", "timeUntilAiring"], "startDate": ["year", "month", "day"], "endDate": ["year", "month", "day"],
    "pageInfo": ["total", "currentPage", "hasNextPage", "perPage"], "name": ["full", "native", "userPreferred"], "image": ["large", "medium"],
}


def ordered(value, path=""):
    """Return value with every object's keys in the fixed order. Lists keep their order; unlisted keys follow the listed ones."""
    if isinstance(value, list):
        return [ordered(v, path) for v in value]
    if not isinstance(value, dict):
        return value
    spec = ORDER_BY_PATH.get(path) or ORDER_BY_KEY.get(path.rsplit(".", 1)[-1]) or []
    rank = {k: i for i, k in enumerate(spec)}
    keys = sorted(value, key=lambda k: rank.get(k, len(rank)))  # stable
    return {k: ordered(value[k], f"{path}.{k}" if path else k) for k in keys}


LIST_KEYS = ("id", "title", "bannerImage", "format", "season", "seasonYear", "episodes", "duration", "status", "averageScore", "meanScore", "popularity", "favourites", "genres", "tags", "source", "countryOfOrigin", "isAdult", "nextAiringEpisode", "startDate", "endDate")


def fresh_airing(x, now=None):
    """timeUntilAiring is relative to the moment the record was stored, so it goes stale. Recompute it from airingAt."""
    a = x.get("nextAiringEpisode")
    if isinstance(a, dict) and a.get("airingAt") is not None:
        x["nextAiringEpisode"] = {**a, "timeUntilAiring": max(0, a["airingAt"] - (now or int(time.time())))}
    return x


def slim(x):
    """The slim list shape: no description, characters, relations or recommendations, and main studios only."""
    cover = x.get("coverImage") or {}
    main = x.get("mainStudios")
    if main is None:  # older record without main studios: animation studios are the closest match
        main = {"nodes": [{"name": m.get("name"), "isAnimationStudio": m.get("isAnimationStudio")} for m in (x.get("studios") or {}).get("nodes") or [] if m.get("isAnimationStudio")]}
    out = {k: x[k] for k in LIST_KEYS if k in x}
    out["coverImage"] = {k: cover[k] for k in (("large", "extraLarge") if STRICT else ("large", "extraLarge", "color")) if k in cover}
    out["studios"] = main
    return ordered(fresh_airing(out))


def _pick(d, keys):
    return {k: d[k] for k in keys if k in d}


def _node(n, keys, cover):
    if not isinstance(n, dict):
        return n
    out = _pick(n, keys)
    if isinstance(out.get("coverImage"), dict):
        out["coverImage"] = _pick(out["coverImage"], cover)
    return out


RELATION_INFO_KEYS = ("id", "title", "coverImage", "format", "type", "status", "episodes", "meanScore")
RELATION_ROUTE_KEYS = ("id", "title", "coverImage", "bannerImage", "format", "type", "status", "episodes", "chapters", "meanScore", "averageScore", "popularity", "startDate")
REC_INFO_KEYS = ("id", "title", "coverImage", "format", "episodes", "status", "meanScore", "averageScore")
REC_ROUTE_KEYS = ("id", "title", "coverImage", "bannerImage", "format", "episodes", "status", "meanScore", "averageScore", "popularity", "genres", "startDate")


def strict_edges(edges, keys, cover):
    return [{**e, "node": _node(e.get("node"), keys, cover)} for e in edges or []] if STRICT else edges


def strict_recs(nodes, keys, cover):
    return [{**r, "mediaRecommendation": _node(r.get("mediaRecommendation"), keys, cover)} for r in nodes or []] if STRICT else nodes


def strict_info(m):
    """Trim a stored record to the exact field set of Miruro's /info."""
    if not STRICT:
        return m
    m.pop("updatedAt", None)
    for e in (m.get("characters") or {}).get("edges") or []:
        n = e.get("node")
        if isinstance(n, dict):
            if isinstance(n.get("name"), dict): n["name"] = _pick(n["name"], ("full", "native"))
            if isinstance(n.get("image"), dict): n["image"] = _pick(n["image"], ("large",))
    if isinstance(m.get("relations"), dict):
        m["relations"]["edges"] = strict_edges(m["relations"].get("edges"), RELATION_INFO_KEYS, ("large",))
    if isinstance(m.get("recommendations"), dict):
        m["recommendations"]["nodes"] = strict_recs(m["recommendations"].get("nodes"), REC_INFO_KEYS, ("large",))
    return m


def clean(x):
    """Drop bookkeeping keys that are stored with a record but are not part of the public shape."""
    x.pop("mainStudios", None); x.pop("_extrasAt", None)
    return x


# ---- links to other titles (relations, recommendations): adult hiding and cover colours ----
def linked_nodes(x):
    for e in (x.get("relations") or {}).get("edges") or []:
        yield e.get("node") or {}
    for r in (x.get("recommendations") or {}).get("nodes") or []:
        yield r.get("mediaRecommendation") or {}


def linked_ids(items):
    """Ids of every title the given records link to."""
    return {n["id"] for x in items for n in linked_nodes(x) if n.get("id")}


def hide_adult(items, adult_ids):
    """Drop linked titles that are flagged adult (by the stored flag or by membership of adult_ids)."""
    hidden = lambda n: bool(n) and bool(n.get("isAdult") or n.get("id") in adult_ids)
    for x in items:
        if isinstance(x.get("relations"), dict):
            x["relations"]["edges"] = [e for e in x["relations"].get("edges") or [] if not hidden(e.get("node"))]
        if isinstance(x.get("recommendations"), dict):
            x["recommendations"]["nodes"] = [r for r in x["recommendations"].get("nodes") or [] if not hidden(r.get("mediaRecommendation"))]


def colour_targets(items):
    """{id: [coverImage dicts]} for linked titles whose cover has no colour yet."""
    nodes = {}
    for x in items:
        for n in linked_nodes(x):
            if n.get("id") and isinstance(n.get("coverImage"), dict) and "color" not in n["coverImage"]:
                nodes.setdefault(n["id"], []).append(n["coverImage"])
    return nodes


def apply_colours(targets, colours):
    for i, color in colours.items():
        for cover in targets.get(i, ()):
            cover["color"] = color

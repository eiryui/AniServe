/* Endpoint reference: the single source of truth for the landing page.
   Add a route in app.py, then add its entry here. The index, the live count
   and the examples all read from this file. */
(function () {
  const PAGING = [
    { name: "page", type: "int", def: "1", desc: "Page number, starting at 1." },
    { name: "per_page", type: "int", def: "20", desc: "Results per page, 1 to 50. Values outside that range return 422." },
  ];
  const detailPaging = [PAGING[0], { name: "per_page", type: "int", def: "all", desc: "Results per page, 1 to 50. Leave it out to get every item in one response (page 1, perPage = total). Values outside 1 to 50 return 422." }];
  const paging = (perPage, max) => [PAGING[0], { name: "per_page", type: "int", def: String(perPage), desc: "Results per page, 1 to " + max + ". Values outside that range return 422." }];

  const ADULT = { name: "isAdult", type: "bool", def: "false", desc: "<code>true</code> includes adult titles alongside the rest. <code>false</code> or omitted hides them, including from relations and recommendations." };

  const filterParams = (perPage) => [
              { name: "q", type: "string", desc: "Title text, same matching as /search. Must not be empty." },
              { name: "genre", type: "string, repeatable", desc: "Genre name such as <code>Action</code>. See /genres." },
              { name: "tag", type: "string, repeatable", desc: "Tag name such as <code>Isekai</code>. See /tags." },
              { name: "year", type: "int", desc: "AniList season year." },
              { name: "season", type: "enum", desc: "<code>WINTER</code> <code>SPRING</code> <code>SUMMER</code> <code>FALL</code>" },
              { name: "format", type: "enum", desc: "<code>TV</code> <code>TV_SHORT</code> <code>MOVIE</code> <code>SPECIAL</code> <code>OVA</code> <code>ONA</code> <code>MUSIC</code>" },
              { name: "status", type: "enum", desc: "<code>FINISHED</code> <code>RELEASING</code> <code>NOT_YET_RELEASED</code> <code>CANCELLED</code> <code>HIATUS</code>" },
              { name: "sort", type: "enum", def: "POPULARITY_DESC", desc: "<code>NAME_DESC</code> is most first and <code>NAME_ASC</code> is least first. Names: <code>POPULARITY</code> <code>SCORE</code> <code>TRENDING</code> <code>START_DATE</code> <code>FAVOURITES</code> <code>UPDATED_AT</code> <code>ID</code>. <code>SCORE</code> orders by AniList\u0027s average score and <code>START_DATE</code> by the real start date. Missing values always sort last, ties fall back to popularity then id, and an unknown value returns 400." },
              ...paging(perPage, 50),
              ADULT];

  window.ANISERVE_DOCS = {
    groups: [
      {
        title: "Search and filter",
        icon: "i-search",
        endpoints: [
          {
            id: "search", path: "/search",
            summary: "Title search across romaji, English, native and synonyms.",
            desc: "Case-insensitive match against every title and synonym stored for a show. Every word you type has to appear, in any order, so <code>kyojin shingeki</code> still finds Shingeki no Kyojin. Results are sorted by popularity and use the slim list shape described under Collections. Every <code>coverImage</code> in the API includes the cover's accent <code>color</code>.",
            params: [{ name: "query", type: "string", req: true, desc: "Text to look for." }, ...PAGING, ADULT],
            example: "/search?query=naruto&per_page=5",
          },
          {
            id: "suggestions", path: "/suggestions",
            summary: "Autocomplete: up to 8 slim results for a search box.",
            desc: "Returns a <code>suggestions</code> array with <code>id</code>, <code>title</code>, <code>title_romaji</code>, <code>poster</code>, <code>format</code>, <code>status</code>, <code>year</code> and <code>episodes</code>. With <code>API_STRICT_MIRURO_SHAPE=false</code> each result also carries <code>coverImage</code>.",
            params: [{ name: "query", type: "string", req: true, desc: "Text typed so far." }, ADULT],
            example: "/suggestions?query=frieren",
          },
          {
            id: "filter", path: "/filter",
            summary: "Combine text, genres, tags, year, season, format and status.",
            desc: "Every filter is optional and they combine with AND. Repeat <code>genre</code> or <code>tag</code> to require several; a show must carry all of them. <code>season</code>, <code>format</code> and <code>status</code> are case-insensitive.",
            params: filterParams(20),
            example: "/filter?genre=Action&genre=Comedy&year=2024&sort=SCORE_DESC&per_page=5",
          },
          {
            id: "multi-search", path: "/multi-search",
            summary: "Same filters as /filter with 30 results per page by default.",
            desc: "Accepts exactly the same parameters as <code>/filter</code>. The only difference is the default <code>per_page</code>, which is 30 here.",
            params: filterParams(30),
            example: "/multi-search?q=one%20piece",
          },
        ],
      },
      {
        title: "Collections",
        icon: "i-trend",
        endpoints: [
          {
            id: "trending", path: "/trending",
            summary: "Sorted by AniList's trending score.",
            params: [...PAGING, ADULT], example: "/trending?per_page=10",
          },
          {
            id: "popular", path: "/popular",
            summary: "Sorted by all-time popularity.",
            params: [...PAGING, ADULT], example: "/popular?per_page=10",
          },
          {
            id: "recent", path: "/recent",
            summary: "Currently releasing titles, newest start date first.",
            params: [...PAGING, ADULT], example: "/recent?per_page=10",
          },
          {
            id: "upcoming", path: "/upcoming",
            summary: "Announced titles that have not released yet.",
            params: [...PAGING, ADULT], example: "/upcoming?per_page=10",
          },
          {
            id: "spotlight", path: "/spotlight",
            summary: "The 10 trending titles, as a bare <code>results</code> array.",
            desc: "Takes only <code>isAdult</code> and returns <code>{ \"results\": [...] }</code> without the pagination fields.",
            params: [ADULT],
            example: "/spotlight",
          },
          {
            id: "schedule", path: "/schedule",
            summary: "Upcoming airings, soonest first, with a countdown.",
            desc: "Lists titles that have a next episode coming up, ordered by air time. Each title appears once, with its next episode. Each item adds <code>next_episode</code>, <code>airingAt</code> (unix seconds) and <code>timeUntilAiring</code> (seconds). With <code>API_STRICT_MIRURO_SHAPE=false</code> it also carries the flat fields <code>color</code>, <code>banner</code> and <code>synopsis</code>. It uses the slim list shape.",
            params: [...PAGING, ADULT], example: "/schedule?per_page=10",
          },
        ],
      },
      {
        title: "Detail pages",
        icon: "i-film",
        endpoints: [
          {
            id: "info", path: "/info/{id}",
            summary: "The full stored record for one anime.",
            desc: "Includes titles, artwork, scores, studios, trailer, dates, relations, recommendations, the first 25 characters (Japanese voice actors), staff, external links, streaming episodes and score stats. Staff, links, streaming episodes and stats are whatever is stored so far; the request itself never calls AniList. A title that is not fully stored yet returns them empty, with the header <code>X-Index-Status: partial</code>. Returns 404 if the id is not in the database yet, or if it is an adult title and <code>isAdult</code> is not true.",
            params: [{ name: "id", type: "int", req: true, desc: "AniList id, in the path." }, ADULT],
            example: "/info/20",
          },
          {
            id: "characters", path: "/anime/{id}/characters",
            summary: "Characters with their voice actors.",
            desc: "Characters ordered by role then relevance, with every voice actor language. The first 25 come straight from the catalog record; later pages are read from the database. Without <code>per_page</code> every character is returned in one response. Items not indexed yet are left out and the response carries <code>X-Index-Status: partial</code>; <code>hasNextPage</code> only promises items that exist.",
            params: [{ name: "id", type: "int", req: true, desc: "AniList id, in the path." }, ...detailPaging, ADULT],
            example: "/anime/20/characters",
          },
          {
            id: "relations", path: "/anime/{id}/relations",
            summary: "Sequels, prequels, adaptations and other related media.",
            desc: "Returns every relation unless you pass <code>per_page</code>, which also adds <code>page</code>, <code>perPage</code>, <code>total</code> and <code>hasNextPage</code> to the body.",
            params: [{ name: "id", type: "int", req: true, desc: "AniList id, in the path." }, ...detailPaging, ADULT],
            example: "/anime/20/relations",
          },
          {
            id: "recommendations", path: "/anime/{id}/recommendations",
            summary: "Community recommendations, all of them unless you set per_page.",
            desc: "Without <code>per_page</code> every recommendation is returned: a title with a <code>total</code> of 100 returns all 100. The first 25 come straight from the catalog record; the rest are read from the database (<code>X-Index-Status: partial</code> until they are stored). <code>total</code> is the real number of recommendations.",
            params: [{ name: "id", type: "int", req: true, desc: "AniList id, in the path." }, ...detailPaging, ADULT],
            example: "/anime/21/recommendations",
          },
        ],
      },
      {
        title: "Catalog and service",
        icon: "i-database",
        endpoints: [
          {
            id: "list", path: "/list",
            summary: "Every stored title with its full record, sorted by id.",
            desc: "Dumps the whole catalog in ascending <code>id</code> order with the full stored record for each title. Like every other route, adult titles are hidden unless <code>isAdult=true</code>. Page through it with <code>page</code> until <code>hasNextPage</code> is false; <code>per_page</code> is capped at 500 and a larger value returns 422. Add <code>ids_only=true</code> to get just the ids.",
            params: [
              { name: "page", type: "int", def: "1", desc: "Page number, starting at 1." },
              { name: "per_page", type: "int", def: "100", desc: "Results per page, 1 to 500." },
              { name: "ids_only", type: "bool", def: "false", desc: "<code>true</code> returns a plain array of ids instead of full records." },
              ADULT,
            ],
            example: "/list?per_page=5",
          },
          {
            id: "genres", path: "/genres",
            summary: "The supported genre list.",
            desc: "Returns <code>{ results: [{ name, isAdult }] }</code>. The list is fixed and shared with the Eiryui frontend (read from <code>genres-tags.js</code>). <code>isAdult</code> is true for Hentai, which is only listed with <code>isAdult=true</code>.",
            params: [ADULT],
            example: "/genres",
          },
          {
            id: "tags", path: "/tags",
            summary: "The supported tag list.",
            desc: "Same shape as /genres, from the same fixed list. <code>isAdult</code> is always false for tags.",
            params: [ADULT],
            example: "/tags",
          },
          {
            id: "docs", path: "/docs", aliases: ["/swagger", "/redoc", "/openapi.json"],
            summary: "Interactive API docs and the OpenAPI schema.",
            desc: "<code>/docs</code> and <code>/swagger</code> open Swagger UI, <code>/redoc</code> opens ReDoc, and <code>/openapi.json</code> is the machine-readable schema for every data route and parameter.",
            example: "/docs",
          },
          {
            id: "health", path: "/health",
            summary: "Service and database status.",
            desc: "Reports <code>status</code>, <code>environment</code> and <code>database</code> (<code>connected</code> or <code>unavailable</code>). With <code>APP_ENVIRONMENT=development</code> it also returns a <code>debug</code> block with catalog counts and job timestamps.",
            example: "/health",
          },
        ],
      },
    ],
  };
})();

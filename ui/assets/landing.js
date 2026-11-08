(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const ICON = (n, cls) => '<svg class="i' + (cls ? " " + cls : "") + '"><use href="/assets/icons.svg#' + n + '"/></svg>';
  const safe = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
  };

  /* ── theme ── */
  function syncThemeIcon() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    document.querySelectorAll("[data-theme-toggle] use").forEach((u) => u.setAttribute("href", "/assets/icons.svg#" + (dark ? "i-sun" : "i-moon")));
  }
  syncThemeIcon();
  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-theme-toggle]")) return;
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    safe.set("aniserve-theme", next);
    syncThemeIcon();
  });

  /* ── copy buttons ── */
  function copyText(text, btn) {
    const done = () => {
      const label = btn.querySelector("span"), old = label ? label.textContent : "";
      btn.classList.add("done");
      if (label) label.textContent = "Copied";
      setTimeout(() => { btn.classList.remove("done"); if (label) label.textContent = old; }, 1400);
    };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
      ta.remove();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy]");
    if (!b) return;
    const v = b.getAttribute("data-copy-text");
    const src = v == null ? document.querySelector(b.getAttribute("data-copy")) : null;
    copyText(v != null ? v : src ? src.innerText : "", b);
  });

  /* ── highlighting ── */
  function hlJSON(text) {
    const tok = (line) => esc(line).replace(
      /(&quot;)(?:\\.|(?!&quot;).)*?\1(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
      (m, q, colon, lit) => {
        if (q) return colon ? '<span class="tk-k">' + m.slice(0, -colon.length) + "</span>" + colon : '<span class="tk-s">' + m + "</span>";
        if (lit) return '<span class="tk-b">' + m + "</span>";
        return '<span class="tk-n">' + m + "</span>";
      });
    return text.split("\n").map((l) => (l.trim().startsWith("//") ? '<span class="tk-c">' + esc(l) + "</span>" : tok(l))).join("\n");
  }
  function hlShell(text) {
    return esc(text).replace(
      /(?<=^|\s)(#.*)$|^([A-Z][A-Z0-9_]*)(?==)|\b(curl|cd|cp|pip|uvicorn|docker|build|run|HTTP)\b/gm,
      (m, comment, key, cmd) => comment ? '<span class="tk-c">' + m + "</span>" : key ? '<span class="tk-k">' + m + "</span>" : '<span class="tk-b">' + m + "</span>");
  }

  /* ── mobile menu ── */
  const mb = $("#menu-btn"), mm = $("#mobile-menu");
  mb.addEventListener("click", () => { const o = mm.classList.toggle("open"); mb.setAttribute("aria-expanded", o); });
  mm.addEventListener("click", (e) => { if (e.target.tagName === "A") { mm.classList.remove("open"); mb.setAttribute("aria-expanded", "false"); } });

  /* ── endpoint reference, rendered from endpoints.js ── */
  const D = window.ANISERVE_DOCS;
  const all = D.groups.flatMap((g) => g.endpoints);
  $("#n-endpoints").textContent = all.reduce((n, e) => n + 1 + (e.aliases ? e.aliases.length : 0), 0);
  const param = (p) =>
    '<div class="prm"><div class="nm"><code>' + esc(p.name) + "</code><small>" + esc(p.type) +
    (p.req ? ' · <i>required</i>' : "") + (p.def ? " · default " + esc(p.def) : "") + '</small></div><div class="ds">' + p.desc + "</div></div>";
  const renderGroup = (g) =>
    '<div class="grp"><h3>' + ICON(g.icon) + esc(g.title) + "</h3>" +
    g.endpoints.map((e) => {
      const curl = 'curl "$BASE' + e.example + '"';
      return '<details class="ep" id="ep-' + e.id + '"><summary><span class="method">GET</span><span class="path">' + esc(e.path) +
        (e.aliases ? e.aliases.map((a) => '<span class="alias">also ' + esc(a) + "</span>").join("") : "") + "</span>" +
        ICON("i-chevron", "go") + '<span class="sum">' + e.summary + "</span></summary>" +
        '<div class="ep-body">' + (e.desc ? "<p>" + e.desc + "</p>" : "") +
        (e.params ? '<div class="params">' + e.params.map(param).join("") + "</div>" : "") +
        '<div class="try"><code>' + esc(curl) + '</code><button class="copy" data-copy-text="' + esc(curl) + '">' + ICON("i-copy") + "<span>Copy</span></button>" +
        '<a class="open" href="' + esc(e.example) + '" target="_blank" rel="noopener">' + ICON("i-external") + "Run</a></div></div></details>";
    }).join("") + "</div>";
  /* two independent columns, so opening one endpoint never shifts the other side */
  const half = Math.ceil(D.groups.length / 2);
  $("#groups").innerHTML = [D.groups.slice(0, half), D.groups.slice(half)].map((col) => '<div class="gcol">' + col.map(renderGroup).join("") + "</div>").join("");
  /* open the endpoint named in the URL hash, e.g. /#ep-filter */
  function openHash() {
    const el = location.hash && document.getElementById(location.hash.slice(1));
    if (el && el.tagName === "DETAILS") { el.open = true; el.scrollIntoView(); }
  }
  openHash(); addEventListener("hashchange", openHash);

  /* ── tabbed code samples ── */
  function tabs(rootSel, preSel, samples) {
    const root = $(rootSel), pre = $(preSel);
    const show = (k) => { pre.innerHTML = (samples[k].json ? hlJSON : hlShell)(samples[k].text); };
    show(Object.keys(samples)[0]);
    root.addEventListener("click", (e) => {
      const t = e.target.closest("[data-key]"); if (!t) return;
      root.querySelectorAll("[data-key]").forEach((b) => b.setAttribute("aria-selected", b === t));
      show(t.dataset.key);
    });
  }
  tabs("#sample", "#sample-pre", {
    page: { json: true, text: `// GET /trending?page=1&per_page=2
{
  "page": 1,
  "perPage": 2,
  "total": 1234,
  "hasNextPage": true,
  "results": [
    {
      "id": 154587,
      "title": {
        "romaji": "Sousou no Frieren",
        "english": "Frieren: Beyond Journey's End"
      },
      "format": "TV",
      "status": "FINISHED",
      "genres": ["Adventure", "Drama", "Fantasy"]
    }
  ]
}` },
    limit: { json: false, text: `HTTP/1.1 429 Too Many Requests
Retry-After: 23
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 23

{"detail": "API rate limit exceeded. Try again after the requested window.", "retry_after": 23}

# X-RateLimit-Reset is seconds until the current window ends.` },
    error: { json: true, text: `// GET /info/999999999  ->  404
{ "detail": "Anime not found" }

// any data route without a database  ->  503
{ "detail": "catalog database unavailable" }` },
  });
  tabs("#run-code", "#run-pre", {
    local: { text: `cd AniServe
cp .env.example .env     # set DATABASE_URL
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001

# open http://localhost:8001` },
    docker: { text: `docker build -t aniserve .
docker run -d --name aniserve \\
  -p 8001:8001 \\
  --env-file .env \\
  aniserve

# DATABASE_URL must be reachable from inside the container.
# The container honours $PORT (default 8001).` },
    env: { text: `# Required: a PostgreSQL filled by AniList Indexer (tables in schema.sql)
DATABASE_URL=postgresql://user:password@host:5432/anilist_index

# Server
ALLOWED_ORIGINS=*
PORT=8001                          # read by the Docker CMD only
APP_ENVIRONMENT=production         # production | development
DATABASE_POOL_SIZE=10
THREAD_POOL_SIZE=32

# Client rate limit (RPM 0 = off)
API_RATE_LIMIT_RPM=120
API_RATE_LIMIT_WINDOW_SECONDS=60
API_RATE_LIMIT_TRUST_PROXY=false   # true only behind a trusted proxy

# Response shape
API_STRICT_MIRURO_SHAPE=true       # false keeps the richer stored fields

# Same list, with notes, in .env.example` },
  });

  /* ── live title count ── */
  const nTitles = $("#n-titles");
  fetch("/filter?per_page=1&isAdult=true").then((r) => { if (!r.ok) throw 0; return r.json(); })
    .then((d) => { nTitles.textContent = Number(d.total || 0).toLocaleString(); })
    .catch(() => { nTitles.textContent = "–"; });

  /* ── hero: live poster shelf from /trending ── */
  const shelf = $("#shelf"), meta = $("#console-meta"), foot = $("#console-foot");
  const lane = (inner, rev) => '<div class="lane' + (rev ? " rev" : "") + '"><div class="lane-track">' + inner + "</div></div>";
  const ghosts = () => '<figure class="card ghost">' + ICON("i-film") + "</figure>";
  shelf.innerHTML = lane(Array(14).fill(ghosts()).join("")) + lane(Array(14).fill(ghosts()).join(""), true);
  const t0 = performance.now();
  fetch("/trending?per_page=20").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }).then((d) => {
    const items = (d.results || []).filter((m) => m.coverImage && m.coverImage.large);
    if (!items.length) {
      meta.textContent = "200 OK · 0 results"; meta.className = "console-meta ok";
      foot.textContent = "The database has no titles yet. Fill it with AniList Indexer (or load your own data), then refresh.";
      return;
    }
    const card = (m, i) => {
      const t = (m.title && (m.title.english || m.title.romaji)) || "Untitled";
      const sub = [m.format, m.seasonYear, m.meanScore ? "★ " + m.meanScore : ""].filter(Boolean).join(" · ");
      return '<figure class="card"><img loading="lazy" src="' + esc(m.coverImage.large) + '" alt="' + esc(t) + ' poster" width="112" height="168"><span class="rk">#' + (i + 1) +
        "</span><figcaption><span>" + esc(t) + "</span><small>" + esc(sub) + "</small></figcaption></figure>";
    };
    // Two distinct rows: ranks 1-10 on the first lane, 11-20 on the second.
    const half = Math.ceil(items.length / 2);
    const a = items.slice(0, half).map((m, i) => card(m, i)), b = items.slice(half).map((m, i) => card(m, half + i));
    const row = (list) => list.join("") + list.join("");
    shelf.innerHTML = lane(row(a)) + lane(row(b.length ? b : a), true);
    meta.textContent = "200 OK · " + items.length + " results · " + Math.round(performance.now() - t0) + " ms";
    meta.className = "console-meta ok";
  }).catch((err) => {
    meta.textContent = "request failed"; meta.className = "console-meta bad";
    foot.textContent = "Could not reach /trending (" + err.message + "). The database may be unavailable or empty.";
  });
})();

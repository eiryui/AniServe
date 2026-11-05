/* AniServe UI: vanilla JS, no build step. All API text is inserted with textContent, never as HTML. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var ORIGIN = location.origin;

  function el(tag, attrs) {
    var n = document.createElement(tag);
    for (var k in attrs || {}) {
      if (k === "text") n.textContent = attrs[k];
      else if (k === "class") n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    }
    for (var i = 2; i < arguments.length; i++) { var c = arguments[i]; if (c != null) n.append(c); }
    return n;
  }
  function safeUrl(u) { return typeof u === "string" && /^(https?:|data:image\/)/.test(u) ? u : null; }
  function setPoster(node, u) { var s = safeUrl(u); if (s) node.style.backgroundImage = "url(" + JSON.stringify(s) + ")"; }
  function titleOf(x) { var t = (x && x.title) || {}; return t.english || t.romaji || t.native || ("Title " + (x && x.id)); }
  function nice(s) { return s ? String(s).toLowerCase().replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }) : ""; }
  function fmtNum(n) { return n == null ? "–" : Number(n).toLocaleString(); }
  function ago(iso) {
    if (!iso) return "never";
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return "just now"; if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " days ago";
  }
  var toastTimer;
  function toast(msg) { var t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove("show"); }, 1600); }
  function copy(text, msg) {
    var done = function () { toast(msg || "Copied"); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
    function fallback() { var a = el("textarea", { "aria-hidden": "true" }); a.value = text; a.style.cssText = "position:fixed;opacity:0"; document.body.append(a); a.select(); try { document.execCommand("copy"); done(); } catch (e) { toast("Copy failed: select the text manually"); } a.remove(); }
  }
  function getJSON(path) { return fetch(path).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }

  /* ---------- theme ---------- */
  $("#theme").addEventListener("click", function () {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("aniserve-theme", next); } catch (e) {}
  });

  /* ---------- status + tally ---------- */
  getJSON("/health").then(function (h) {
    var s = $("#status"); s.classList.add(h.status === "online" ? "ok" : "bad");
    $("span", s).textContent = h.status === "online" ? "Database connected" : "Database unavailable";
    if (h.problem) s.title = h.problem;
  }).catch(function () { $("#status").classList.add("bad"); $("#status span").textContent = "Server unreachable"; });

  /* ---------- wall: the catalogue as namako-kabe tiles ---------- */
  var wallTitles = [];
  function rng(seed) { return function () { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }; }
  function buildWall(animate) {
    var wall = $("#wall"); wall.replaceChildren(); wall.classList.toggle("static", !animate);
    var W = wall.clientWidth, T = W < 640 ? 76 : 108, H = W < 640 ? 300 : 392;
    wall.style.setProperty("--t", T + "px"); wall.style.setProperty("--wall-h", H + "px");
    var rows = Math.ceil(H / (T / 2)) + 2, cols = Math.ceil(W / T) + 2, spots = [];
    for (var r = 0; r < rows; r++) for (var c = 0; c < cols; c++) spots.push({ r: r, c: c, x: c * T - T + (r % 2 ? T / 2 : 0), y: r * (T / 2) - T / 2 });
    var rand = rng(11), order = spots.map(function (_, i) { return i; });
    for (var i = order.length - 1; i > 0; i--) { var j = Math.floor(rand() * (i + 1)); var tmp = order[i]; order[i] = order[j]; order[j] = tmp; }
    var visible = order.filter(function (k) { var s = spots[k]; return s.x > -T / 2 && s.x < W - T / 2 && s.y > -T / 4 && s.y < H - T / 2; });
    var owner = {}; visible.slice(0, wallTitles.length).forEach(function (k, n) { owner[k] = wallTitles[n]; });
    var tip = el("div", { "class": "wall-tip", "aria-hidden": "true" }); wall.append(tip);
    spots.forEach(function (s, k) {
      var item = owner[k], t;
      if (item) {
        t = el("button", { "class": "tile has", type: "button", "aria-label": "Open " + titleOf(item) });
        setPoster(t, (item.coverImage || {}).large);
        t.addEventListener("click", function () { openRecord(item.id, true); });
        var show = function () { tip.textContent = titleOf(item); tip.style.left = (s.x + T / 2) + "px"; tip.style.top = (s.y + T / 2) + "px"; tip.classList.add("show"); };
        t.addEventListener("mouseenter", show); t.addEventListener("focus", show);
        t.addEventListener("mouseleave", function () { tip.classList.remove("show"); }); t.addEventListener("blur", function () { tip.classList.remove("show"); });
      } else { t = el("span", { "class": "tile", "aria-hidden": "true" }); }
      t.style.left = s.x + "px"; t.style.top = s.y + "px";
      t.style.setProperty("--d", String(Math.round(s.r * 38 + s.c * 22 + rand() * 90)));
      wall.append(t);
    });
  }
  getJSON("/popular?per_page=50").then(function (d) { wallTitles = (d.results || []).filter(function (x) { return safeUrl((x.coverImage || {}).large); }); }).catch(function () {}).then(function () { buildWall(true); });
  var rz; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { buildWall(false); }, 200); });

  /* ---------- explorer ---------- */
  var ENUM = {
    season: ["WINTER", "SPRING", "SUMMER", "FALL"],
    format: ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC"],
    status: ["FINISHED", "RELEASING", "NOT_YET_RELEASED", "CANCELLED", "HIATUS"],
    sort: ["POPULARITY_DESC", "POPULARITY_ASC", "SCORE_DESC", "SCORE_ASC", "TRENDING_DESC", "TRENDING_ASC", "START_DATE_DESC", "START_DATE_ASC", "FAVOURITES_DESC", "FAVOURITES_ASC", "UPDATED_AT_DESC", "UPDATED_AT_ASC", "ID_DESC", "ID_ASC"]
  };
  var FIELD = {
    query: { label: "Title text", wide: true, req: true, ph: "naruto" }, q: { label: "Title text", wide: true, ph: "optional" },
    genre: { label: "Genre", sel: "genres" }, tag: { label: "Tag", list: "tags", ph: "Isekai" }, year: { label: "Season year", type: "number", ph: "2024" },
    season: { label: "Season", sel: ENUM.season }, format: { label: "Format", sel: ENUM.format }, status: { label: "Status", sel: ENUM.status },
    sort: { label: "Sort", sel: ENUM.sort, wide: true, def: "POPULARITY_DESC" }, id: { label: "AniList id", type: "number", wide: true, req: true, ph: "9000003" },
    per_page: { label: "Per page", type: "number", def: "20" }
  };
  var ROUTES = {
    popular: { label: "Popular", path: "/popular", f: ["per_page"], list: true },
    search: { label: "Search", path: "/search", f: ["query", "per_page"], list: true },
    filter: { label: "Filter", path: "/filter", f: ["q", "genre", "tag", "year", "season", "format", "status", "sort", "per_page"], list: true },
    trending: { label: "Trending", path: "/trending", f: ["per_page"], list: true },
    schedule: { label: "Schedule", path: "/schedule", f: ["per_page"], list: true },
    info: { label: "Record", path: "/info/{id}", f: ["id"], record: true },
    recent: { label: "Recent", path: "/recent", f: ["per_page"], list: true },
    upcoming: { label: "Upcoming", path: "/upcoming", f: ["per_page"], list: true },
    spotlight: { label: "Spotlight", path: "/spotlight", f: [], list: true },
    suggestions: { label: "Suggestions", path: "/suggestions", f: ["query"] },
    characters: { label: "Characters", path: "/anime/{id}/characters", f: ["id", "per_page"], dflt: { per_page: "25" } },
    relations: { label: "Relations", path: "/anime/{id}/relations", f: ["id"] },
    recommendations: { label: "Recommendations", path: "/anime/{id}/recommendations", f: ["id", "per_page"], dflt: { per_page: "10" } },
    list: { label: "Full list", path: "/list", f: ["per_page"], dflt: { per_page: "20" } }
  };
  var PRIMARY = ["popular", "search", "filter", "trending", "schedule", "info"];
  var S = { route: "popular", v: {}, page: 1, adult: false, view: "titles", data: null, raw: "" };
  var OPTIONS = { genres: [], tags: [] };
  var runTimer, runSeq = 0;

  function defFor(route, name) { var r = ROUTES[route]; return (r.dflt && r.dflt[name]) || FIELD[name].def || ""; }
  function buildUrl() {
    var r = ROUTES[S.route], path = r.path, qs = [];
    path = path.replace("{id}", encodeURIComponent(S.v.id || "{id}"));
    r.f.forEach(function (n) {
      if (n === "id") return; var val = S.v[n];
      if (val != null && val !== "" && val !== defFor(S.route, n)) qs.push(n + "=" + encodeURIComponent(val));
    });
    if (S.page > 1 && !/spotlight|suggestions|relations|info/.test(r.path)) qs.push("page=" + S.page);
    if (S.adult) qs.push("isAdult=true");
    return path + (qs.length ? "?" + qs.join("&") : "");
  }
  function missingRequired() { return ROUTES[S.route].f.filter(function (n) { return FIELD[n].req && !S.v[n]; }); }

  function renderTabs() {
    var tabs = $("#routeTabs"); tabs.replaceChildren();
    PRIMARY.forEach(function (id) {
      var b = el("button", { type: "button", role: "tab", "aria-selected": String(S.route === id), text: ROUTES[id].label });
      b.addEventListener("click", function () { setRoute(id); }); tabs.append(b);
    });
    var more = el("select", { "aria-label": "Other routes", "class": "more" }), first = el("option", { value: "", text: "Other routes" });
    more.append(first);
    Object.keys(ROUTES).filter(function (k) { return PRIMARY.indexOf(k) < 0; }).forEach(function (k) { var o = el("option", { value: k, text: ROUTES[k].label }); if (S.route === k) o.selected = true; more.append(o); });
    more.style.cssText = "padding:6px 8px;border:1px solid var(--line);background:var(--panel);font:inherit;font-size:14px;font-weight:500;color:var(--ink)";
    more.addEventListener("change", function () { if (more.value) setRoute(more.value); });
    tabs.append(more);
  }
  function renderFields() {
    var box = $("#fields"); box.replaceChildren();
    ROUTES[S.route].f.forEach(function (n) {
      var d = FIELD[n], id = "f-" + n, wrap = el("div", { "class": "field" + (d.wide ? " wide" : "") }), input;
      wrap.append(el("label", { "for": id, text: d.label + (d.req ? " (required)" : "") }));
      if (d.sel) {
        input = el("select", { id: id }); input.append(el("option", { value: "", text: "Any" }));
        (d.sel === "genres" ? OPTIONS.genres : d.sel).forEach(function (o) { input.append(el("option", { value: o, text: d.sel === "genres" ? o : nice(o) })); });
        if (n === "sort") input.firstChild.remove();
      } else {
        input = el("input", { id: id, type: d.type || "text", placeholder: d.ph || "" });
        if (d.list) { input.setAttribute("list", "dl-" + n); }
        if (d.type === "number") { input.min = "1"; if (n === "per_page") input.max = S.route === "recommendations" ? "25" : "50"; }
      }
      var cur = S.v[n]; input.value = cur != null && cur !== "" ? cur : defFor(S.route, n);
      S.v[n] = input.value;
      var onchg = function () { S.v[n] = input.value.trim(); S.page = 1; update(input.tagName === "SELECT" ? 0 : 380); };
      input.addEventListener(input.tagName === "SELECT" ? "change" : "input", onchg);
      wrap.append(input); box.append(wrap);
      if (d.list && !$("#dl-" + n)) { var dl = el("datalist", { id: "dl-" + n }); OPTIONS.tags.forEach(function (t) { dl.append(el("option", { value: t })); }); document.body.append(dl); }
    });
    $("#isAdult").checked = S.adult;
  }
  function setRoute(id, vals, keepPage) {
    S.route = id; S.v = vals || {}; if (!keepPage) S.page = 1;
    var r = ROUTES[id]; S.pref = r.record ? "record" : r.list ? "titles" : "json";
    renderTabs(); renderFields(); update(0);
  }
  function openRecord(id, scroll) { setRoute("info", { id: String(id) }); if (scroll) $("#explorer").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); }
  $("#isAdult").addEventListener("change", function (e) { S.adult = e.target.checked; S.page = 1; update(0); });
  $("#slip").addEventListener("submit", function (e) { e.preventDefault(); update(0); });
  $("#copyUrl").addEventListener("click", function () { copy(ORIGIN + buildUrl(), "URL copied"); });
  $("#copyCurl").addEventListener("click", function () { copy('curl -s "' + ORIGIN + buildUrl() + '"', "curl command copied"); });
  $("#prev").addEventListener("click", function () { if (S.page > 1) { S.page--; update(0); } });
  $("#next").addEventListener("click", function () { S.page++; update(0); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { var i = $("#fields input, #fields select"); if (i) { e.preventDefault(); i.focus(); } }
  });

  function update(delay) {
    var url = buildUrl(); $("#callno").textContent = url;
    history.replaceState(null, "", "#" + url);
    clearTimeout(runTimer); runTimer = setTimeout(run, delay);
  }

  function run() {
    var miss = missingRequired(), out = $("#out"), seq = ++runSeq;
    $("#pager").hidden = true;
    if (miss.length) { S.data = null; renderViews(); $("#meta").replaceChildren(); out.replaceChildren(el("p", { "class": "empty", text: "Fill in " + miss.map(function (n) { return FIELD[n].label.toLowerCase(); }).join(" and ") + " to send this request." })); return; }
    var url = buildUrl(), t0 = performance.now();
    fetch(url).then(function (r) {
      return r.text().then(function (txt) { return { r: r, txt: txt, ms: performance.now() - t0 }; });
    }).then(function (x) {
      if (seq !== runSeq) return;
      var data = null; try { data = JSON.parse(x.txt); } catch (e) {}
      S.data = data; S.raw = x.txt; S.ok = x.r.ok;
      var m = $("#meta"); m.replaceChildren();
      var add = function (k, v, cls) { m.append(el("span", {}, k + " ", el("b", { text: v, "class": cls || "" }))); };
      add("Status", String(x.r.status), x.r.ok ? "" : "err"); add("Round trip", Math.round(x.ms) + " ms");
      var srv = x.r.headers.get("X-AniServe-Ms"); if (srv) add("Server", srv + " ms");
      add("Size", (new Blob([x.txt]).size / 1024 < 1 ? new Blob([x.txt]).size + " B" : (new Blob([x.txt]).size / 1024).toFixed(1) + " kB"));
      var rem = x.r.headers.get("X-RateLimit-Remaining"); if (rem != null) add("Requests left", rem);
      renderViews(); renderOut(); renderPager();
    }).catch(function () {
      if (seq !== runSeq) return;
      $("#meta").replaceChildren(el("span", {}, "Status ", el("b", { "class": "err", text: "no response" })));
      S.data = null; renderViews(); out.replaceChildren(problem("Could not reach the server", "Check that AniServe is running and that this page was opened from it."));
    });
  }
  function problem(a, b) { return el("div", { "class": "problem" }, el("strong", { text: a }), el("p", { text: b })); }

  function listOf(d) { return d && (Array.isArray(d.results) ? d.results : Array.isArray(d.suggestions) ? d.suggestions : null); }
  function viewsAvailable() {
    var r = ROUTES[S.route], d = S.data, v = [];
    if (d && S.ok && r.record && d.id) v.push(["record", "Record"]);
    else if (d && S.ok && listOf(d)) v.push(["titles", "Titles"]);
    v.push(["json", "JSON"]); return v;
  }
  function renderViews() {
    var v = viewsAvailable(), box = $("#views"); box.replaceChildren();
    var want = v.some(function (x) { return x[0] === S.pref; }) ? S.pref : v[0][0];
    S.view = want;
    v.forEach(function (x) {
      var b = el("button", { type: "button", role: "tab", "aria-selected": String(S.view === x[0]), text: x[1] });
      b.addEventListener("click", function () { S.pref = x[0]; renderViews(); renderOut(); }); box.append(b);
    });
  }
  function renderPager() {
    var d = S.data, p = $("#pager"); if (!d || !S.ok || typeof d.page !== "number" || d.total == null) { p.hidden = true; return; }
    p.hidden = false; $("#prev").disabled = d.page <= 1; $("#next").disabled = !d.hasNextPage;
    $("#pageinfo").textContent = "Page " + d.page + " of " + Math.max(1, Math.ceil(d.total / d.perPage)) + ", " + fmtNum(d.total) + " in total";
  }

  function renderOut() {
    var out = $("#out"); out.replaceChildren();
    if (!S.ok && S.data) { out.append(problem("The server answered " + (S.data.detail ? "with an error" : "unexpectedly"), typeof S.data.detail === "string" ? S.data.detail : "See the JSON tab for the full response.")); return; }
    if (S.view === "titles") return renderCards(out);
    if (S.view === "record") return renderRecord(out);
    var btn = el("button", { type: "button", "class": "copy-json", text: "Copy JSON" });
    btn.addEventListener("click", function () { copy(S.data ? JSON.stringify(S.data, null, 2) : S.raw, "JSON copied"); });
    var pre = el("pre", { "class": "json", tabindex: "0", "aria-label": "JSON response" }); pre.innerHTML = S.data == null ? esc(S.raw.slice(0, 4000)) : highlight(S.data);
    out.append(btn, pre);
  }
  function renderCards(out) {
    var items = listOf(S.data) || [];
    if (!items.length) { out.append(el("p", { "class": "empty", text: "No titles match. Loosen a filter or clear the search text." })); return; }
    var ul = el("ul", { "class": "grid" });
    items.forEach(function (x) {
      var p = el("span", { "class": "poster" }); setPoster(p, (x.coverImage || {}).large || x.poster);
      if (x.status) p.append(el("span", { "class": "flag " + x.status, text: nice(x.status) }));
      var year = x.year || (x.startDate || {}).year || x.seasonYear;
      var meta = [x.format && nice(x.format).replace(/^Tv$/, "TV"), year, x.episodes ? x.episodes + " ep" : null].filter(Boolean).join(", ");
      if (x.next_episode) meta = "Episode " + x.next_episode + " airs " + new Date(x.airingAt * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      var b = el("button", { "class": "card", type: "button" }, p, el("strong", { text: titleOf(x) }), el("small", { text: meta }));
      b.addEventListener("click", function () { openRecord(x.id); }); ul.append(el("li", {}, b));
    });
    out.append(ul);
  }
  function renderRecord(out) {
    var m = S.data, t = m.title || {}, poster = el("span", { "class": "poster" }); setPoster(poster, (m.coverImage || {}).large);
    var studio = ((m.studios || {}).nodes || [])[0];
    var facts = [["Format", nice(m.format)], ["Status", nice(m.status)], ["Episodes", m.episodes], ["Season", m.season ? nice(m.season) + " " + (m.seasonYear || "") : m.seasonYear], ["Average score", m.averageScore], ["Popularity", m.popularity != null ? fmtNum(m.popularity) : null], ["Studio", studio && studio.name], ["Source", nice(m.source)]];
    var dl = el("dl", { "class": "facts" }); facts.forEach(function (f) { if (f[1]) dl.append(el("div", {}, el("dt", { text: f[0] }), el("dd", { text: String(f[1]) }))); });
    var chips = el("ul", { "class": "chips" }); (m.genres || []).concat((m.tags || []).slice(0, 6).map(function (x) { return x.name; })).forEach(function (g) { chips.append(el("li", { text: g })); });
    var desc = (m.description || "").replace(/<br\s*\/?>/gi, "\n").replace(/<[^>]+>/g, "");
    var cast = el("ul", { "class": "cast" });
    ((m.characters || {}).edges || []).slice(0, 12).forEach(function (e) {
      var va = (e.voiceActors || [])[0]; cast.append(el("li", {}, ((e.node || {}).name || {}).full || "Unknown", va ? el("span", { text: " voiced by " + ((va.name || {}).full || "") }) : null));
    });
    var side = el("div", {}, el("h3", { text: titleOf(m) }), el("p", { "class": "alt", text: [t.romaji, t.native].filter(function (x) { return x && x !== titleOf(m); }).join(", ") }), dl, chips,
      desc ? el("p", { "class": "desc", text: desc }) : null, cast.children.length ? el("h4", { text: "Cast on file", style: "margin:0 0 6px;font-size:14px" }) : null, cast.children.length ? cast : null);
    out.append(el("div", { "class": "record" }, poster, side));
  }

  /* ---------- JSON highlighting (display only; Copy JSON gives the full text) ---------- */
  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function highlight(data) {
    var txt = JSON.stringify(data, function (k, v) { return typeof v === "string" && v.length > 120 ? v.slice(0, 48) + "… [" + v.length.toLocaleString() + " characters]" : v; }, 2), cut = "";
    if (txt.length > 300000) { txt = txt.slice(0, 300000); cut = '\n<span class="z">… response shortened here; use Copy JSON for all of it</span>'; }
    return esc(txt).replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g, function (m, str, colon, lit) {
      if (str) return colon ? '<span class="k">' + str + "</span>" + colon : '<span class="s">' + str + "</span>";
      if (lit) return '<span class="b">' + m + "</span>"; return '<span class="n">' + m + "</span>";
    }) + cut;
  }

  /* ---------- ledger ---------- */
  function chart(title, rows, max) {
    var ul = el("ul", { "class": "bars" });
    rows.forEach(function (r) {
      var fill = el("i", { "class": "fill" }); fill.style.width = Math.max(2, (r.count / max) * 100) + "%";
      ul.append(el("li", {}, el("span", { "class": "lab", text: r.label, title: r.label }), el("span", { "class": "track" }, fill), el("span", { "class": "num", text: fmtNum(r.count) })));
    });
    return ul;
  }
  getJSON("/stats").then(function (s) {
    var set = function (k, v) { var d = $('[data-k="' + k + '"]'); if (d) d.textContent = v; };
    set("entries", fmtNum(s.entries)); set("airing", fmtNum(s.airing)); set("years", s.firstYear ? s.firstYear + "–" + s.lastYear : "–"); set("last", ago(s.lastIndexedAt));
    var box = $("#charts"); box.replaceChildren();
    var mk = function (title, build) { var c = el("div", { "class": "chart" }, el("h3", { text: title })); build(c); box.append(c); };
    var rowsOf = function (a, f) { return a.map(function (x) { return { label: f(x.key), count: x.count }; }); };
    var mx = function (a) { return Math.max.apply(null, a.map(function (x) { return x.count; }).concat(1)); };
    mk("Titles by decade", function (c) { var r = rowsOf(s.decades, function (k) { return k + "s"; }); c.append(chart("", r, mx(r))); });
    mk("Most common genres", function (c) { var r = rowsOf(s.genres, String); c.append(chart("", r, mx(r))); });
    mk("Status and format", function (c) { var a = rowsOf(s.status, function (k) { return nice(k) || "Unknown"; }), b = rowsOf(s.format, function (k) { return k ? nice(k).replace(/^Tv/, "TV") : "Unknown"; }), m = Math.max(mx(a), mx(b)); c.append(chart("", a, m)); c.append(el("div", { style: "height:14px" })); c.append(chart("", b, m)); });
    var jobs = $("#jobs"); jobs.replaceChildren();
    Object.keys(s.refreshJobs || {}).forEach(function (k) { jobs.append(el("span", {}, nice(k.replace(/_/g, " ")) + " ", el("b", { text: ago(s.refreshJobs[k]) }))); });
  }).catch(function () { $("#charts").replaceChildren(el("p", { "class": "empty", text: "The catalogue counts could not be loaded. Check the status light above." })); });

  /* ---------- routes + recipes ---------- */
  var GROUPS = [
    ["Find titles", [
      ["/search?query=", "Title text matched against romaji, English, native names and synonyms. Every word must appear, in any order.", "search", { query: "hoshi" }],
      ["/filter", "Genre, tag, year, season, format, status and sort, all optional and combined with AND. /multi-search is the same with 30 per page.", "filter", {}],
      ["/suggestions?query=", "Up to 8 slim results for an autocomplete box.", "suggestions", { query: "kaze" }]]],
    ["Browse", [
      ["/popular", "Most popular first.", "popular", {}], ["/trending", "Highest trending score first.", "trending", {}],
      ["/recent", "Currently releasing, newest start date first.", "recent", {}], ["/upcoming", "Announced but not yet released.", "upcoming", {}],
      ["/spotlight", "The 10 trending titles, without paging fields.", "spotlight", {}], ["/schedule", "Next episode per title, soonest first, with airingAt and timeUntilAiring.", "schedule", {}]]],
    ["One title", [
      ["/info/{id}", "The full stored record. Staff, links, streaming episodes and stats appear only when the dump holds them.", "info", { id: "9000003" }],
      ["/anime/{id}/characters", "Characters and voice actors. Pages past the first 25 appear only when the dump holds them.", "characters", { id: "9000003", per_page: "10" }],
      ["/anime/{id}/relations", "Sequels, prequels, side stories and other related media.", "relations", { id: "9000003" }],
      ["/anime/{id}/recommendations", "Recommendations ordered by rating.", "recommendations", { id: "9000003" }]]],
    ["Catalogue", [
      ["/genres and /tags", "The fixed genre and tag lists shared with the Eiryui frontend.", "list", {}],
      ["/list", "Every title with its full record, ordered by id, up to 500 per page. Add ids_only=true for just the ids.", "list", {}],
      ["/stats", "The counts behind the ledger above. Cached for 30 seconds.", null],
      ["/export", "The whole catalogue streamed as NDJSON, or one JSON array with format=json.", null],
      ["/health", "Service and database status.", null]]]
  ];
  var list = $("#routeList");
  GROUPS.forEach(function (g) {
    var sec = el("div", { "class": "rgroup" }, el("h3", { text: g[0] }));
    g[1].forEach(function (r) {
      var row = el("div", { "class": "rrow" }, el("code", { text: "GET " + r[0] }), el("p", { text: r[1] }));
      if (r[2]) { var b = el("button", { type: "button", text: "Try", "aria-label": "Try " + r[0] }); b.addEventListener("click", function () { setRoute(r[2], Object.assign({}, r[3])); $("#explorer").scrollIntoView({ behavior: "smooth" }); }); row.append(b); }
      else { row.append(el("a", { href: r[0].split(/[ ?]/)[0], target: "_blank", rel: "noopener", text: "Open" })); }
      sec.append(row);
    });
    list.append(sec);
  });
  var RECIPES = [
    ["Download over HTTP", "Streams every record. One export runs at a time.", 'curl -o catalog.ndjson "' + ORIGIN + '/export"\ncurl -o catalog.json "' + ORIGIN + '/export?format=json"'],
    ["Run the exporter", "Reads the database directly, no web server needed.", "python export.py catalog.ndjson\npython export.py catalog.json --format json\npython export.py shelf/ --format files"],
    ["Serve it with Docker", "Mount the dump file or point at a hosted database.", "docker build -t aniserve .\ndocker run -p 8002:8002 \\\n  -e DATABASE_URL=sqlite:////data/anilist.sqlite \\\n  -v $PWD/data:/data:ro aniserve"]
  ];
  RECIPES.forEach(function (r) {
    var b = el("button", { type: "button", text: "Copy" }); b.addEventListener("click", function () { copy(r[2], "Copied"); });
    $("#recipes").append(el("div", { "class": "recipe" }, el("h3", { text: r[0] }), el("p", { text: r[1] }), el("pre", { text: r[2], tabindex: "0" }), b));
  });

  /* ---------- boot ---------- */
  function parseHash() {
    var h = decodeURIComponent(location.hash.slice(1)); if (h.charAt(0) !== "/") return null;
    var u = new URL(h, ORIGIN), id = null, vals = {};
    Object.keys(ROUTES).some(function (k) {
      var re = new RegExp("^" + ROUTES[k].path.replace(/[.*+?^$()|[\]\\]/g, "\\$&").replace("{id}", "(\\d+)") + "$"), m = u.pathname.match(re);
      if (m) { id = k; if (m[1]) vals.id = m[1]; return true; } return false;
    });
    if (!id) return null;
    ROUTES[id].f.forEach(function (n) { if (n !== "id" && u.searchParams.has(n)) vals[n] = u.searchParams.get(n); });
    return { id: id, vals: vals, page: parseInt(u.searchParams.get("page") || "1", 10) || 1, adult: u.searchParams.get("isAdult") === "true" };
  }
  window.addEventListener("hashchange", function () {
    var h = parseHash(); if (!h || ("#" + buildUrl()) === decodeURIComponent(location.hash)) return;
    S.adult = h.adult; S.page = h.page; setRoute(h.id, h.vals, true);
  });
  Promise.all([getJSON("/genres").catch(function () { return {}; }), getJSON("/tags").catch(function () { return {}; })]).then(function (r) {
    OPTIONS.genres = (r[0].results || []).map(function (x) { return x.name; }); OPTIONS.tags = (r[1].results || []).map(function (x) { return x.name; });
    var h = parseHash();
    if (h) { S.adult = h.adult; S.page = h.page; setRoute(h.id, h.vals, true); } else setRoute("popular");
  });
})();

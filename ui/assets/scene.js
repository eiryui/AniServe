/* AniServe landing: scroll-driven 3D scenes. No libraries, CSS 3D transforms only.
   The hero ring uses /trending; the tunnel uses the 50 most popular titles from /popular. Without database titles, stand-in posters are drawn.

   Hero geometry is measured, not hard-coded: layout() looks at the free space next to the copy (or above it on narrow screens),
   sizes the ring to fit it, and renders() moves logo + glow + ring to the middle of the screen as you scroll while the ring grows.
   Every frame the ring scale is clamped by a projected bounding box of the whole ring, so the circle is never cropped by the screen. */
(function () {
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v == null || v !== v ? a : v));
  const smooth = (a, b, v) => { const u = clamp((v - a) / (b - a), 0, 1); return u * u * (3 - 2 * u); };
  const inOut = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const FALLBACK = ["Frieren", "Dandadan", "Solo Leveling", "Jujutsu Kaisen", "Spy x Family", "Vinland Saga", "Mushishi", "Cowboy Bebop", "Haikyuu!!", "Violet Evergarden", "Steins;Gate", "Mob Psycho 100", "Fruits Basket", "Monster", "Clannad", "Kaguya-sama", "Blue Lock", "Chainsaw Man", "Bocchi the Rock!", "Oshi no Ko"];
  let items = FALLBACK.map((t, i) => ({ t, sub: "Anime", h: 255 + ((i * 29) % 90), img: "" }));
  let popular = items; /* tunnel cards: replaced by the 50 most popular titles when the database answers */

  const face = (m) => '<div class="f" style="--h:' + m.h + '">' + (m.img ? '<img loading="lazy" alt="" src="' + esc(m.img) + '">' : "<b>" + esc(m.t[0]) + "</b>") +
    "<span><em>" + esc(m.t) + "</em><small>" + esc(m.sub) + "</small></span></div>";
  const hero = $("#top"), stage = $(".stage", hero), ring = $("#ring"), ringWrap = $(".ring-wrap", hero), halo = $(".halo", hero), rail = $("#rail");
  const logo = $("#logo3d"), copy = $("#hero-copy"), topbar = $(".topbar");
  const T = 50, GAP = 150;
  let N = 14, STEP = 360 / N, pcs = [];
  function build() {
    const at = (i) => items[i % items.length];
    STEP = 360 / N;
    ring.style.setProperty("--step", STEP + "deg");
    ring.innerHTML = Array.from({ length: N }, (_, i) => '<div class="pc" style="--i:' + i + '">' + face(at(i)).replace('class="f"', 'class="f a"') + face(at(i)).replace('class="f"', 'class="f b"') + "</div>").join("");
    rail.innerHTML = Array.from({ length: T }, (_, i) => {
      const side = i % 2 ? 1 : -1, x = side * (24 + ((i * 37) % 17)), y = ((i * 53) % 7 - 3) * 9;
      return '<div class="tc" style="--x:' + x + ";--y:" + y + ";--z:" + i * GAP + '">' + face(popular[i % popular.length]) + "</div>";
    }).join("");
    pcs = $$(".pc", ring);
  }
  build();

  /* live titles */
  const toItems = (d) => (d.results || []).filter((m) => m.coverImage && m.coverImage.large).map((m, i) => ({
    t: (m.title && (m.title.english || m.title.romaji)) || "Untitled",
    sub: [m.format, m.seasonYear, m.meanScore ? "★ " + m.meanScore : ""].filter(Boolean).join(" · "),
    h: 255 + ((i * 29) % 90), img: m.coverImage.large,
  }));
  const load = (url, apply) => fetch(url).then((r) => { if (!r.ok) throw 0; return r.json(); }).then((d) => {
    const live = toItems(d);
    if (live.length) { apply(live); build(); }
  }).catch(() => { /* keep the stand-in posters */ });
  load("/trending?per_page=20", (live) => { items = live; });
  load("/popular?per_page=50", (live) => { popular = live; });

  /* ── ring geometry ───────────────────────────────────────────────────────────────────────────
     Card k sits at rotateY(a) translateZ(R) on a ring that is scaled, spun (rotateY), tilted (rotateX) and then seen
     through perspective f. extent() projects every card corner for a continuous set of angles, which is the same as
     "any spin", and returns the screen box of the whole ring relative to its centre. */
  const RAD = Math.PI / 180, SMP = 64;
  function extent(R, cw, s, f, tilt) {
    const ct = Math.cos(tilt * RAD), st = Math.sin(tilt * RAD);
    let l = 1e9, r = -1e9, t = 1e9, b = -1e9;
    for (let k = 0; k < SMP; k++) {
      const a = k / SMP * 2 * Math.PI, ca = Math.cos(a), sa = Math.sin(a);
      for (let c = 0; c < 4; c++) {
        const u = (c & 1 ? 0.5 : -0.5) * cw, v = (c & 2 ? 0.75 : -0.75) * cw;
        const x = (u * ca + R * sa) * s, y0 = v * s, z0 = (-u * sa + R * ca) * s;
        const y = y0 * ct - z0 * st, z = y0 * st + z0 * ct, d = f - z;
        if (d < f * 0.2) return { l: -1e9, r: 1e9, t: -1e9, b: 1e9 }; /* too close to the camera: treat as not fitting */
        const q = f / d;
        l = Math.min(l, x * q); r = Math.max(r, x * q); t = Math.min(t, y * q); b = Math.max(b, y * q);
      }
    }
    return { l, r, t, b };
  }
  /* largest scale at which the ring, centred on (cx, cy), still sits fully inside the screen (with margin m) */
  function maxScale(g, tilt, cx, cy) {
    let lo = 0.2, hi = 8;
    for (let i = 0; i < 18; i++) {
      const s = (lo + hi) / 2, e = extent(g.R, g.cw, s, g.f, tilt);
      if (cx + e.l >= g.m && cx + e.r <= g.W - g.m && cy + e.t >= g.m && cy + e.b <= g.H - g.m) lo = s; else hi = s;
    }
    return lo;
  }

  const G = { W: 0, H: 0, stack: false, R: 200, cw: 70, f: 1200, cx0: 0, cy0: 0, cx1: 0, cy1: 0, S: 1, m: 8, tilt0: -28, tilt1: -12, p: 0 };
  let navH = 64, stageH = 600, lastW = 0, lastH = 0;

  function layout() {
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    document.documentElement.style.setProperty("--u", (rootPx / 16).toFixed(4));
    navH = topbar.offsetHeight;
    const W = stage.clientWidth, H = stage.clientHeight;
    if (!W || !H) return;
    stageH = H; lastW = innerWidth; lastH = innerHeight;
    const stack = W < 900 || W / H < 1.05; /* phones and portrait tablets: copy at the bottom, ring above it */
    hero.classList.toggle("stack", stack);
    const wantN = stack ? 10 : 14; /* fewer, bigger posters when the ring has to fit a narrow screen */
    if (wantN !== N) { N = wantN; build(); }

    /* free space for the ring: right of the copy, or above it */
    const pad = Math.round(Math.min(W, H) * 0.035);
    let zl, zr, zt, zb;
    if (stack) { zl = copy.offsetLeft; zr = W - zl; zt = pad; zb = Math.max(zt + 140, copy.offsetTop - pad); }
    else { zl = copy.offsetLeft + copy.offsetWidth + rootPx * 2.5; zr = W - copy.offsetLeft; zt = pad; zb = H - pad; }
    const cx0 = (zl + zr) / 2, cy0 = (zt + zb) / 2;

    /* biggest ring that fits that space (all lengths scale with R, so one unit-ring measurement is enough), then 14% headroom to grow */
    const rho = stack ? 0.5 : 0.36, fr = 7; /* card width / ring radius, perspective / ring radius */
    const e = extent(1, rho, 1, fr, G.tilt0);
    const Rmax = Math.min((zr - cx0) / e.r, (cx0 - zl) / -e.l, (zb - cy0) / e.b, (cy0 - zt) / -e.t);
    const R = Math.max(40, Math.min(Rmax * 0.86, rootPx * 26));
    Object.assign(G, { W, H, stack, R, cw: rho * R, f: fr * R, cx0, cy0, cx1: W / 2, cy1: H / 2, m: Math.max(6, Math.min(W, H) * 0.012) });
    G.S = Math.max(1, maxScale(G, G.tilt1, G.cx1, G.cy1) * 0.998);

    ring.style.setProperty("--R", R.toFixed(1) + "px");
    ring.style.setProperty("--cw", G.cw.toFixed(1) + "px");
    ringWrap.style.setProperty("--persp", G.f.toFixed(0) + "px");
    logo.style.setProperty("--lg", (R * 0.78).toFixed(1) + "px");
    halo.style.setProperty("--hs", (R * 2.9).toFixed(1) + "px");
    ringWrap.style.left = ringWrap.style.top = halo.style.left = halo.style.top = "0";
    renderHero(G.p, performance.now());
  }

  /* ── scroll progress per scene (0 to 1 while its sticky stage is pinned below the nav) ── */
  const scenes = $$(".scene"), reveals = $$("[data-r]"), caps = $$(".cap");
  let mx = 0, my = 0, sx = 0, sy = 0, vh = innerHeight;
  addEventListener("pointermove", (e) => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; }, { passive: true });
  let rz = 0;
  addEventListener("resize", () => {
    vh = innerHeight;
    if (innerWidth === lastW && Math.abs(innerHeight - lastH) < 160) return; /* mobile address bar sliding in/out: keep the geometry steady */
    cancelAnimationFrame(rz); rz = requestAnimationFrame(layout);
  });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(layout); /* web fonts change the copy's width */
  addEventListener("load", layout);

  function renderHero(p, t) {
    G.p = p;
    const q = clamp(p / 0.74, 0, 1), qe = q * q * (3 - 2 * q);   /* zoom phase */
    const m = inOut(clamp(p / 0.38, 0, 1));                       /* move-to-centre phase */
    const cx = G.cx0 + (G.cx1 - G.cx0) * m, cy = G.cy0 + (G.cy1 - G.cy0) * m;
    const tilt = G.tilt0 + (G.tilt1 - G.tilt0) * qe;              /* camera drops a little as the ring opens up */
    const s = Math.min(1 + (G.S - 1) * qe, maxScale(G, tilt, cx, cy) * 0.998); /* never bigger than the screen can show whole */
    const spin = p * 330 + (still ? 20 : t / 1000 * 7);

    ringWrap.style.transform = "translate3d(" + cx.toFixed(1) + "px," + cy.toFixed(1) + "px,0)";
    ring.style.transform = "rotateX(" + tilt.toFixed(2) + "deg) rotateY(" + spin.toFixed(2) + "deg) scale(" + s.toFixed(4) + ")";
    /* a card turning edge-on to the camera draws as a jagged sliver, so fade it out while it does */
    for (let k = 0; k < pcs.length; k++) {
      const c = Math.abs(Math.cos((k * STEP + spin) * RAD)), u = clamp(c / 0.3, 0, 1);
      pcs[k].style.setProperty("--edge", (u * u * (3 - 2 * u)).toFixed(3));
    }
    /* the cards fade out exactly as the ring reaches full-screen size, so the circle is whole right up to the moment it disappears
       (opacity goes on the cards, not the ring: opacity on a 3D parent would flatten it) */
    ring.style.setProperty("--fade", (1 - smooth(0.6, 1, q)).toFixed(3));

    const grow = 1 + 0.5 * inOut(clamp(p / 0.95, 0, 1));
    /* right at the start of the scroll the logo steps forward, in front of the ring's near cards (scaled back by the perspective it gains, so its size stays as designed) */
    const zf = smooth(0, 0.1, p) * 1.2 * G.R * s * Math.cos(tilt * RAD), mag = G.f / (G.f - zf);
    logo.style.transform = "translate3d(0," + (-6 * (1 - m)).toFixed(2) + "%," + zf.toFixed(1) + "px) rotateY(" + (sx * 26 + Math.sin(t / 1800) * 5).toFixed(2) + "deg) rotateX(" + (-sy * 20).toFixed(2) + "deg) scale(" + (grow / mag).toFixed(4) + ")";
    logo.style.opacity = (1 - smooth(0.74, 0.97, p)).toFixed(3);
    halo.style.transform = "translate3d(" + cx.toFixed(1) + "px," + cy.toFixed(1) + "px,0) scale(" + (s * (1 + 0.35 * smooth(0.7, 1, p))).toFixed(3) + ")";
    halo.style.opacity = (1 - smooth(0.78, 1, p)).toFixed(3);

    copy.style.opacity = clamp(1 - p * 9, 0, 1);
    copy.style.transform = G.stack ? "translateY(" + (-p * 140).toFixed(1) + "px)" : "translateY(calc(-50% - " + (p * 140).toFixed(1) + "px))";
    copy.style.pointerEvents = p > 0.1 ? "none" : "";
  }

  function frame(t) {
    sx += (mx - sx) * 0.06; sy += (my - sy) * 0.06;
    for (const s of scenes) {
      const r = s.getBoundingClientRect();
      if (r.bottom < -50 || r.top > vh + 50) continue;
      const p = clamp((navH - r.top) / Math.max(1, r.height - stageH), 0, 1);
      s.style.setProperty("--p", p.toFixed(4));
      if (s === hero) renderHero(p, t);
      else if (s.id === "tunnel") {
        const u = parseFloat(document.documentElement.style.getPropertyValue("--u")) || 1;
        rail.style.transform = "translateZ(" + (p * ((T - 1) * GAP + 700) * u) + "px) rotateY(" + sx * 6 + "deg) rotateX(" + -sy * 4 + "deg)";
        caps.forEach((c, k) => c.style.setProperty("--o", clamp(1.15 - Math.abs(p - (0.12 + k * 0.38)) * 6.5, 0, 1).toFixed(3)));
      }
    }
    for (const el of reveals) {
      const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh) continue;
      el.style.setProperty("--r", clamp((vh - r.top) / (vh * 0.55), 0, 1).toFixed(3));
    }
    requestAnimationFrame(frame);
  }
  layout();
  if (!still) requestAnimationFrame(frame); else { scenes.forEach((s) => s.style.setProperty("--p", 1)); }

  /* pointer tilt on feature tiles */
  $$(".tile").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      const b = el.getBoundingClientRect(), x = (e.clientX - b.left) / b.width - 0.5, y = (e.clientY - b.top) / b.height - 0.5;
      el.style.transform = "perspective(800px) rotateY(" + x * 10 + "deg) rotateX(" + -y * 10 + "deg) translateZ(8px)";
    });
    el.addEventListener("pointerleave", () => { el.style.transform = ""; });
  });
})();

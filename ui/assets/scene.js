/* AniServe landing: scroll-driven 3D scenes. No libraries, CSS 3D transforms only.
   The hero ring uses /trending; the tunnel uses the 50 most popular titles from /popular. Without database titles, stand-in posters are drawn. */
(function () {
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v == null || v !== v ? a : v));
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const FALLBACK = ["Frieren", "Dandadan", "Solo Leveling", "Jujutsu Kaisen", "Spy x Family", "Vinland Saga", "Mushishi", "Cowboy Bebop", "Haikyuu!!", "Violet Evergarden", "Steins;Gate", "Mob Psycho 100", "Fruits Basket", "Monster", "Clannad", "Kaguya-sama", "Blue Lock", "Chainsaw Man", "Bocchi the Rock!", "Oshi no Ko"];
  let items = FALLBACK.map((t, i) => ({ t, sub: "Anime", h: 255 + ((i * 29) % 90), img: "" }));
  let popular = items; /* tunnel cards: replaced by the 50 most popular titles when the database answers */

  const face = (m, i) => '<div class="f" style="--h:' + m.h + '">' + (m.img ? '<img loading="lazy" alt="" src="' + esc(m.img) + '">' : "<b>" + esc(m.t[0]) + "</b>") +
    "<span>" + esc(m.t) + "<small>" + esc(m.sub) + "</small></span></div>";
  const ring = $("#ring"), rail = $("#rail"), N = 14, T = 50, GAP = 150, STEP = 360 / N, TILT = 62, FAN = 22;
  let pcs = [];
  function build() {
    const at = (i) => items[i % items.length];
    ring.innerHTML = Array.from({ length: N }, (_, i) => '<div class="pc" style="--i:' + i + '">' + face(at(i), i) + "</div>").join("");
    rail.innerHTML = Array.from({ length: T }, (_, i) => {
      const side = i % 2 ? 1 : -1, x = side * (24 + ((i * 37) % 17)), y = ((i * 53) % 7 - 3) * 9;
      return '<div class="tc" style="--x:' + x + ";--y:" + y + ";--z:" + i * GAP + '">' + face(popular[i % popular.length], i) + "</div>";
    }).join("");
  }
  build();
  pcs = $$(".pc", ring);

  /* Put every card on the circle, then undo the ring's spin and tilt on the card itself so it always faces the screen
     (nothing turns edge-on, so the ring never breaks up). FAN leans the side cards slightly toward the centre for depth. */
  function place(spin, tilt) {
    for (let k = 0; k < pcs.length; k++) {
      const a = k * STEP, fan = -Math.sin((a + spin) * Math.PI / 180) * FAN;
      pcs[k].style.transform = "rotateY(" + a.toFixed(3) + "deg) translateZ(var(--R)) rotateY(" + (-(a + spin)).toFixed(3) + "deg) rotateX(" + tilt + "deg) rotateY(" + fan.toFixed(2) + "deg)";
    }
  }

  /* live titles */
  const toItems = (d) => (d.results || []).filter((m) => m.coverImage && m.coverImage.large).map((m, i) => ({
    t: (m.title && (m.title.english || m.title.romaji)) || "Untitled",
    sub: [m.format, m.seasonYear, m.meanScore ? "★ " + m.meanScore : ""].filter(Boolean).join(" · "),
    h: 255 + ((i * 29) % 90), img: m.coverImage.large,
  }));
  const load = (url, apply) => fetch(url).then((r) => { if (!r.ok) throw 0; return r.json(); }).then((d) => {
    const live = toItems(d);
    if (live.length) { apply(live); build(); pcs = $$(".pc", ring); }
  }).catch(() => { /* keep the stand-in posters */ });
  load("/trending?per_page=20", (live) => { items = live; });
  load("/popular?per_page=50", (live) => { popular = live; });

  /* scroll progress per scene (0 to 1 while its sticky stage is pinned) */
  const scenes = $$(".scene"), reveals = $$("[data-r]"), logo = $("#logo3d"), copy = $("#hero-copy"), caps = $$(".cap");
  let mx = 0, my = 0, sx = 0, sy = 0, vh = innerHeight;
  const tunnelStage = $("#tunnel .stage"), fit = () => { vh = innerHeight; if (tunnelStage) tunnelStage.style.setProperty("--zs", Math.max(1, innerWidth / 1900).toFixed(3)); };
  fit(); addEventListener("resize", fit);
  addEventListener("pointermove", (e) => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; }, { passive: true });

  function frame(t) {
    sx += (mx - sx) * 0.06; sy += (my - sy) * 0.06;
    for (const s of scenes) {
      const r = s.getBoundingClientRect();
      if (r.bottom < -50 || r.top > vh + 50) continue;
      const p = clamp(-r.top / (r.height - vh), 0, 1);
      s.style.setProperty("--p", p.toFixed(4));
      if (s.id === "top") {
        const spin = p * 330 + (still ? 20 : t / 1000 * 7), tilt = innerWidth > 860 ? TILT : TILT + 2;
        ring.style.transform = "rotateX(" + -tilt + "deg) rotateY(" + spin + "deg) scale(" + (1 + p * p * 2.6).toFixed(3) + ")";
        place(spin, tilt);
        ring.style.setProperty("--fade", clamp(1 - (p - 0.3) * 2.6, 0, 1).toFixed(3)); /* fade the cards, not the ring: opacity on a 3D parent flattens it */
        logo.style.transform = "rotateY(" + (sx * 26 + Math.sin(t / 1800) * 5) + "deg) rotateX(" + (-sy * 20) + "deg) translate3d(0," + (-6 - p * 10) + "%," + (-p * 300) + "px) scale(" + (1 + p * 0.4) + ")";
        logo.style.opacity = clamp(1.5 - p * 1.5, 0, 1);
        copy.style.opacity = clamp(1 - p * 8, 0, 1);
        copy.style.transform = "translateY(calc(" + (innerWidth > 860 ? "-50%" : "0px") + " - " + p * 140 + "px))";
        copy.style.pointerEvents = p > 0.1 ? "none" : "";
      } else if (s.id === "tunnel") {
        rail.style.transform = "translateZ(" + (p * ((T - 1) * GAP + 700) * Math.max(1, innerWidth / 1900)) + "px) rotateY(" + sx * 6 + "deg) rotateX(" + -sy * 4 + "deg)";
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
  if (!still) requestAnimationFrame(frame); else { scenes.forEach((s) => s.style.setProperty("--p", s.id === "top" ? 0 : 1)); place(20, innerWidth > 860 ? TILT : TILT + 2); }

  /* pointer tilt on feature tiles */
  $$(".tile").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      const b = el.getBoundingClientRect(), x = (e.clientX - b.left) / b.width - 0.5, y = (e.clientY - b.top) / b.height - 0.5;
      el.style.transform = "perspective(800px) rotateY(" + x * 10 + "deg) rotateX(" + -y * 10 + "deg) translateZ(8px)";
    });
    el.addEventListener("pointerleave", () => { el.style.transform = ""; });
  });
})();

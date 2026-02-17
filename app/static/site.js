(() => {
  // ---------- Year search ----------
  const search = document.getElementById("hymnSearch");
  if (search) {
    const rows = Array.from(document.querySelectorAll(".hymn-row"));
    const cards = Array.from(document.querySelectorAll(".hymn-card"));

    search.addEventListener("input", () => {
      const q = (search.value || "").trim().toLowerCase();
      rows.forEach(r => {
        const t = r.dataset.title || "";
        r.style.display = (!q || t.includes(q)) ? "" : "none";
      });
      cards.forEach(c => {
        const t = c.dataset.title || "";
        c.style.display = (!q || t.includes(q)) ? "" : "none";
      });
    });
  }

  // ---------- Hymn page ----------
  const audio = document.getElementById("audio");
  const table = document.getElementById("lyricsTable");
  if (!table) return;

  // Use URL path for storage keys (no need for window.STMINA)
  const keyBase = location.pathname;
  const langStoreKey = `langs:${keyBase}`;
  const fontStoreKey = `font:${keyBase}`;

  // --- Language toggles ---
  const toggles = Array.from(document.querySelectorAll(".lang-toggle"));

  function setLangVisible(code, visible) {
    // Only hide/show lyric cells (never the toggle buttons)
    document.querySelectorAll(`#lyricsTable td[data-lang="${code}"]`).forEach(el => {
      el.classList.toggle("is-hidden", !visible);
    });
  }

  function setToggleUI(toggleEl, on) {
    toggleEl.classList.toggle("off", !on);
    const pill = toggleEl.querySelector(".pill");
    if (pill) pill.textContent = on ? "ON" : "OFF";
  }

  function readPrefs() {
    try {
      return JSON.parse(localStorage.getItem(langStoreKey) || "{}");
    } catch {
      return {};
    }
  }

  function writePrefs(prefs) {
    localStorage.setItem(langStoreKey, JSON.stringify(prefs));
  }

  function loadLangPrefs() {
    const prefs = readPrefs();

    toggles.forEach(t => {
      const code = t.dataset.langCode;          // <-- matches your HTML
      const def = (t.dataset.default === "1");
      const cb = t.querySelector(".lang-check");
      if (!code || !cb) return;

      const on = (prefs[code] !== undefined) ? !!prefs[code] : def;
      cb.checked = on;
      setToggleUI(t, on);
      setLangVisible(code, on);
    });
  }

  toggles.forEach(t => {
    const cb = t.querySelector(".lang-check");
    if (!cb) return;

    cb.addEventListener("change", () => {
      const code = t.dataset.langCode;
      const on = cb.checked;

      setToggleUI(t, on);
      setLangVisible(code, on);

      const prefs = readPrefs();
      prefs[code] = on;
      writePrefs(prefs);
    });
  });

  // --- Font size ---
  const root = document.documentElement;
  const plus = document.getElementById("fontPlus");
  const minus = document.getElementById("fontMinus");

  function setFont(px) {
    px = Math.max(12, Math.min(32, px));
    root.style.setProperty("--hymn-font", `${px}px`);
    localStorage.setItem(fontStoreKey, String(px));
  }

  function loadFont() {
    const v = parseInt(localStorage.getItem(fontStoreKey) || "18", 10);
    setFont(Number.isNaN(v) ? 18 : v);
  }

  if (plus) plus.addEventListener("click", () => {
    const cur = parseInt(getComputedStyle(root).getPropertyValue("--hymn-font")) || 18;
    setFont(cur + 1);
  });

  if (minus) minus.addEventListener("click", () => {
    const cur = parseInt(getComputedStyle(root).getPropertyValue("--hymn-font")) || 18;
    setFont(cur - 1);
  });

  // --- Sync highlight + click-to-seek ---
  const rows = Array.from(table.querySelectorAll("tr.seg"));
  const starts = rows.map(r => parseInt(r.dataset.startMs, 10) || 0);

  function findActiveIdx(tMs) {
    let lo = 0, hi = starts.length - 1, ans = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] <= tMs) { ans = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return ans;
  }

  let last = -1;
  function setActive(idx) {
    if (idx === last) return;
    if (last >= 0) rows[last].classList.remove("active");
    rows[idx].classList.add("active");
    rows[idx].scrollIntoView({ block: "center", behavior: "smooth" });
    last = idx;
  }

  rows.forEach((r, i) => {
    r.addEventListener("click", () => {
      if (!audio) return;
      const ms = parseInt(r.dataset.startMs, 10) || 0;
      audio.currentTime = ms / 1000.0;
      audio.play().catch(() => {});
      setActive(i);
    });
  });

  if (audio) {
    audio.addEventListener("timeupdate", () => {
      const tMs = audio.currentTime * 1000;
      setActive(findActiveIdx(tMs));
    });
  }

  // init
  loadLangPrefs();
  loadFont();
})();

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
  const recordingSelect = document.getElementById("recordingSelect");
  const table = document.getElementById("lyricsTable");

  if (!table) return;

  const cfg = window.STMINA || { hymnKey: "default", recordingsCount: 0 };

  // --- Load audio from selected recording ---
  function setAudioFromSelect() {
    if (!audio || !recordingSelect) return;
    const opt = recordingSelect.options[recordingSelect.selectedIndex];
    const url = opt.getAttribute("data-url");
    const rate = parseFloat(opt.getAttribute("data-rate") || "1");
    if (url) {
      audio.src = url;
      audio.playbackRate = rate;
      audio.load();
    }
  }

  if (audio && recordingSelect) {
    recordingSelect.addEventListener("change", () => {
      setAudioFromSelect();
    });
    // initial
    setAudioFromSelect();
  }

  // --- Speed buttons ---
  const speedStoreKey = `speed:${cfg.hymnKey}`;
  const speedChips = Array.from(document.querySelectorAll(".chip[data-speed]"));

  function applySpeed(v) {
    if (!audio) return;
    audio.playbackRate = v;
    localStorage.setItem(speedStoreKey, String(v));
    speedChips.forEach(b => b.classList.toggle("active", b.dataset.speed === String(v)));
  }

  const savedSpeed = parseFloat(localStorage.getItem(speedStoreKey) || "");
  if (audio && !Number.isNaN(savedSpeed)) applySpeed(savedSpeed);

  speedChips.forEach(btn => {
    btn.addEventListener("click", () => {
      const v = parseFloat(btn.dataset.speed || "1");
      applySpeed(v);
    });
  });

  // --- Language toggles ---
  const toggles = Array.from(document.querySelectorAll(".lang-toggle"));
  const langStoreKey = `langs:${cfg.hymnKey}`;

  function setLangVisible(code, visible) {
    document.querySelectorAll(`[data-lang="${code}"]`).forEach(el => {
      el.classList.toggle("is-hidden", !visible);
    });
  }

  function loadLangPrefs() {
    let prefs = {};
    try { prefs = JSON.parse(localStorage.getItem(langStoreKey) || "{}"); } catch {}

    toggles.forEach(t => {
      const code = t.dataset.lang;
      const def = (t.dataset.default === "1");
      const cb = t.querySelector(".lang-check");

      const on = (prefs[code] !== undefined) ? !!prefs[code] : def;
      cb.checked = on;
      t.classList.toggle("off", !on);
      setLangVisible(code, on);
    });
  }

  function saveLangPrefs() {
    const prefs = {};
    toggles.forEach(t => {
      const code = t.dataset.lang;
      prefs[code] = t.querySelector(".lang-check").checked;
    });
    localStorage.setItem(langStoreKey, JSON.stringify(prefs));
  }

  toggles.forEach(t => {
    t.addEventListener("click", () => {
      setTimeout(() => {
        const code = t.dataset.lang;
        const cb = t.querySelector(".lang-check");
        const on = cb.checked;
        t.classList.toggle("off", !on);
        setLangVisible(code, on);
        saveLangPrefs();
      }, 0);
    });
  });

  // --- Font size ---
  const root = document.documentElement;
  const plus = document.getElementById("fontPlus");
  const minus = document.getElementById("fontMinus");
  const fontStoreKey = `font:${cfg.hymnKey}`;

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

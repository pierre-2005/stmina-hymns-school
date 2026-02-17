document.addEventListener("DOMContentLoaded", () => {
  // ---------- Year search ----------
  const search = document.getElementById("hymnSearch");
  if (search) {
    const rows = Array.from(document.querySelectorAll(".hymn-row"));
    const cards = Array.from(document.querySelectorAll(".hymn-card"));

    search.addEventListener("input", () => {
      const q = (search.value || "").trim().toLowerCase();
      rows.forEach(r => {
        const t = (r.dataset.title || "").toLowerCase();
        r.style.display = (!q || t.includes(q)) ? "" : "none";
      });
      cards.forEach(c => {
        const t = (c.dataset.title || "").toLowerCase();
        c.style.display = (!q || t.includes(q)) ? "" : "none";
      });
    });
  }

  // ---------- Hymn page ----------
  const audio = document.getElementById("audio");
  const table = document.getElementById("lyricsTable");
  if (!table) return;

  const keyBase = location.pathname;
  const langStoreKey = `langs:${keyBase}`;
  const fontStoreKey = `font:${keyBase}`;

  // --- Language toggles ---
  const toggles = Array.from(document.querySelectorAll(".lang-toggle"));

  function setLangVisible(code, visible) {
    // ONLY target lyric cells, never the toggle labels
    const cells = document.querySelectorAll(`#lyricsTable td[data-lang="${code}"]`);
    cells.forEach(td => {
      td.classList.toggle("is-hidden", !visible);

      // fallback in case .is-hidden isn't defined in CSS
      if (!visible) td.style.display = "none";
      else td.style.display = "";
    });
  }

  function setToggleUI(toggleEl, on) {
    toggleEl.classList.toggle("off", !on);
    const pill = toggleEl.querySelector(".pill");
    if (pill) pill.textContent = on ? "ON" : "OFF";
  }

  function readPrefs() {
    try { return JSON.parse(localStorage.getItem(langStoreKey) || "{}"); }
    catch { return {}; }
  }

  function writePrefs(prefs) {
    localStorage.setItem(langStoreKey, JSON.stringify(prefs));
  }

  function initLangToggles() {
    const prefs = readPrefs();

    toggles.forEach(t => {
      const code = t.getAttribute("data-lang-code");
      const defOn = (t.getAttribute("data-default") === "1");
      const cb = t.querySelector(".lang-check");
      if (!code || !cb) return;

      const on = (prefs[code] !== undefined) ? !!prefs[code] : defOn;

      cb.checked = on;
      setToggleUI(t, on);
      setLangVisible(code, on);

      // IMPORTANT: listen to checkbox change (reliable)
      cb.addEventListener("change", () => {
        const nowOn = cb.checked;

        setToggleUI(t, nowOn);
        setLangVisible(code, nowOn);

        const next = readPrefs();
        next[code] = nowOn;
        writePrefs(next);
      });
    });
  }

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
  const segRows = Array.from(table.querySelectorAll("tr.seg"));

  // FIX: your HTML uses data-start-ms, so JS must read dataset.startMs OR dataset.startMs? No:
  // dataset.startMs maps to data-start-ms BUT ONLY if the attribute is data-start-ms EXACTLY (it is),
  // however your earlier code used data-start-ms but created parse from dataset.startMs while some browsers can be picky.
  // We'll read it safely using getAttribute.
  const starts = segRows.map(r => {
    const v = r.getAttribute("data-start-ms") || "0";
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : 0;
  });

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
    if (last >= 0) segRows[last].classList.remove("active");
    if (segRows[idx]) {
      segRows[idx].classList.add("active");
      segRows[idx].scrollIntoView({ block: "center", behavior: "smooth" });
      last = idx;
    }
  }

  segRows.forEach((r, i) => {
    r.addEventListener("click", () => {
      if (!audio) return;
      const v = r.getAttribute("data-start-ms") || "0";
      const ms = parseInt(v, 10) || 0;
      audio.currentTime = ms / 1000.0;
      audio.play().catch(() => {});
      setActive(i);
    });
  });

  if (audio) {
    audio.addEventListener("timeupdate", () => {
      setActive(findActiveIdx(audio.currentTime * 1000));
    });
  }

  // init
  initLangToggles();
  loadFont();
});

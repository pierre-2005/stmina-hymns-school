document.addEventListener("DOMContentLoaded", () => {
  initSearch();
  initLanguageControls();
  initLyricFontControls();
  initSoundCloudSync();
});

function initSearch() {
  const search = document.getElementById("hymnSearch");
  if (!search) return;

  const cards = Array.from(document.querySelectorAll(".hymn-card"));
  const empty = document.getElementById("searchEmpty");

  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    let visibleCount = 0;
    cards.forEach((card) => {
      const matches = !query || (card.dataset.title || "").includes(query);
      card.classList.toggle("is-hidden", !matches);
      if (matches) visibleCount += 1;
    });
    if (empty) empty.classList.toggle("is-hidden", visibleCount !== 0);
  }

  search.addEventListener("input", filter);
}

function initLanguageControls() {
  const table = document.getElementById("lyricsTable");
  const wrap = document.getElementById("lyricsWrap");
  if (!table || !wrap) return;

  const toggles = Array.from(document.querySelectorAll(".language-toggle"));
  const version = wrap.dataset.languageVersion || "default";
  const storageKey = `stminahs:languages:${location.pathname}:${version}`;
  const reset = document.getElementById("resetLanguagePrefs");

  function readPreferences() {
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "null");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function setColumn(code, visible) {
    table.querySelectorAll(`td[data-lang="${CSS.escape(code)}"]`).forEach((cell) => {
      cell.classList.toggle("is-hidden", !visible);
    });
  }

  function setToggle(toggle, visible) {
    toggle.classList.toggle("off", !visible);
    const checkbox = toggle.querySelector(".lang-check");
    const state = toggle.querySelector(".toggle-state");
    if (checkbox) checkbox.checked = visible;
    if (state) state.textContent = visible ? "ON" : "OFF";
  }

  function applyDefaults(clearStored = false) {
    if (clearStored) localStorage.removeItem(storageKey);
    const preferences = readPreferences();
    toggles.forEach((toggle) => {
      const code = toggle.dataset.langCode;
      const defaultValue = toggle.dataset.default === "1";
      const visible = Object.prototype.hasOwnProperty.call(preferences, code)
        ? Boolean(preferences[code])
        : defaultValue;
      setToggle(toggle, visible);
      setColumn(code, visible);
    });
  }

  toggles.forEach((toggle) => {
    const checkbox = toggle.querySelector(".lang-check");
    if (!checkbox) return;
    checkbox.addEventListener("change", () => {
      const code = toggle.dataset.langCode;
      const visible = checkbox.checked;
      setToggle(toggle, visible);
      setColumn(code, visible);
      const preferences = readPreferences();
      preferences[code] = visible;
      localStorage.setItem(storageKey, JSON.stringify(preferences));
    });
  });

  reset?.addEventListener("click", () => applyDefaults(true));
  applyDefaults(false);
}

function initLyricFontControls() {
  const table = document.getElementById("lyricsTable");
  if (!table) return;

  const plus = document.getElementById("fontPlus");
  const minus = document.getElementById("fontMinus");
  const storageKey = `stminahs:lyric-font:${location.pathname}`;

  function setSize(size) {
    const clamped = Math.max(13, Math.min(34, size));
    document.documentElement.style.setProperty("--lyric-font-size", `${clamped}px`);
    localStorage.setItem(storageKey, String(clamped));
  }

  const saved = Number.parseInt(localStorage.getItem(storageKey) || "19", 10);
  setSize(Number.isFinite(saved) ? saved : 19);

  plus?.addEventListener("click", () => {
    const current = Number.parseInt(getComputedStyle(document.documentElement).getPropertyValue("--lyric-font-size"), 10) || 19;
    setSize(current + 1);
  });
  minus?.addEventListener("click", () => {
    const current = Number.parseInt(getComputedStyle(document.documentElement).getPropertyValue("--lyric-font-size"), 10) || 19;
    setSize(current - 1);
  });
}

function initSoundCloudSync() {
  const table = document.getElementById("lyricsTable");
  const frames = Array.from(document.querySelectorAll("iframe.sc-player"));
  if (!table || !frames.length || !window.SC?.Widget) return;

  const rows = Array.from(table.querySelectorAll("tr.lyric-row"));
  if (!rows.length) return;

  const starts = rows.map((row) => Number.parseInt(row.dataset.startMs || "0", 10) || 0);
  const widgets = frames.map((frame) => window.SC.Widget(frame));
  let activeWidget = widgets[0];
  let activeRow = -1;
  let lastAutoScroll = 0;

  function findRow(positionMs) {
    let low = 0;
    let high = starts.length - 1;
    let answer = 0;
    while (low <= high) {
      const middle = (low + high) >> 1;
      if (starts[middle] <= positionMs) {
        answer = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    return answer;
  }

  function activate(index, shouldScroll = true) {
    if (!rows[index] || index === activeRow) return;
    if (activeRow >= 0) rows[activeRow].classList.remove("active");
    rows[index].classList.add("active");
    activeRow = index;
    const now = Date.now();
    if (shouldScroll && now - lastAutoScroll > 1400) {
      rows[index].scrollIntoView({ behavior: "smooth", block: "center" });
      lastAutoScroll = now;
    }
  }

  widgets.forEach((widget) => {
    widget.bind(window.SC.Widget.Events.PLAY, () => { activeWidget = widget; });
    widget.bind(window.SC.Widget.Events.PLAY_PROGRESS, (event) => {
      activeWidget = widget;
      activate(findRow(event.currentPosition), true);
    });
    widget.bind(window.SC.Widget.Events.FINISH, () => {
      if (activeRow >= 0) rows[activeRow].classList.remove("active");
      activeRow = -1;
    });
  });

  rows.forEach((row, index) => {
    const seek = () => {
      activeWidget.seekTo(starts[index]);
      activeWidget.play();
      activate(index, false);
    };
    row.addEventListener("click", seek);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        seek();
      }
    });
  });
}

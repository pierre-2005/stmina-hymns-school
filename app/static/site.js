document.addEventListener("DOMContentLoaded", () => {
  initSearch();
  initLanguageControls();
  initLyricFontControls();
  initLyricSeekToggle();
  initRecordingSync();
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

function formatAudioTime(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const whole = Math.floor(safe);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

const LYRIC_SEEK_STORAGE_KEY = "stminahs:lyric-seek-enabled";

function isLyricSeekEnabled() {
  return localStorage.getItem(LYRIC_SEEK_STORAGE_KEY) !== "0";
}

function initLyricSeekToggle() {
  const button = document.getElementById("lyricSeekToggle");
  const table = document.getElementById("lyricsTable");
  const help = document.getElementById("lyricSeekHelp");
  if (!button && !table) return;

  function apply(enabled) {
    if (button) {
      button.textContent = `Lyric seek: ${enabled ? "ON" : "OFF"}`;
      button.setAttribute("aria-pressed", enabled ? "true" : "false");
      button.classList.toggle("is-off", !enabled);
    }
    if (table) {
      table.classList.toggle("lyric-seek-disabled", !enabled);
      table.querySelectorAll("tr.lyric-row").forEach((row) => {
        row.tabIndex = enabled ? 0 : -1;
        row.setAttribute("aria-disabled", enabled ? "false" : "true");
      });
    }
    if (help) {
      help.textContent = enabled
        ? "Lyric seeking is ON. Tap a lyric row to seek the active recording to that timestamp."
        : "Lyric seeking is OFF. Lyric rows will still highlight during playback, but clicking them will not move the recording.";
    }
  }

  apply(isLyricSeekEnabled());

  button?.addEventListener("click", () => {
    const enabled = !isLyricSeekEnabled();
    localStorage.setItem(LYRIC_SEEK_STORAGE_KEY, enabled ? "1" : "0");
    apply(enabled);
    window.dispatchEvent(new CustomEvent("stmina:lyric-seek-change", { detail: { enabled } }));
  });
}

function initRecordingSync() {
  const adapters = [];
  let activePlayer = null;
  let changingPlayer = false;

  function pauseOthers(player) {
    if (changingPlayer) return;
    changingPlayer = true;
    try {
      adapters.forEach((other) => {
        if (other !== player) other.pause?.();
      });
    } finally {
      changingPlayer = false;
    }
  }

  function boundedSeekTarget(targetMs, startMs, endMs, durationMs = 0) {
    const start = Math.max(0, Number(startMs) || 0);
    const duration = Math.max(0, Number(durationMs) || 0);
    let end = Math.max(0, Number(endMs) || 0);
    if (duration > 0) {
      end = end > 0 ? Math.min(end, duration) : duration;
    }

    const target = Math.max(0, Number(targetMs) || 0);

    // Requested behavior: anything before the allowed window jumps to its
    // beginning; anything after the allowed window also wraps to the beginning.
    if (target < start) return start;
    if (end > start && target > end) return start;

    if (duration > 0) return Math.min(target, duration);
    return target;
  }

  // ---------------- Self-hosted waveform players ----------------
  document.querySelectorAll(".wave-player").forEach((container) => {
    const audio = container.querySelector("audio.wave-audio");
    const button = container.querySelector(".wave-play-button");
    const canvas = container.querySelector(".waveform-canvas");
    const seekSurface = container.querySelector(".waveform-wrap");
    const playhead = container.querySelector(".waveform-playhead");
    const currentText = container.querySelector(".wave-current");
    const durationText = container.querySelector(".wave-duration");
    const dataNode = container.querySelector(".waveform-data");
    if (!audio || !button || !canvas || !seekSurface) return;

    let peaks = [];
    try {
      const parsed = JSON.parse(dataNode?.textContent || "[]");
      peaks = Array.isArray(parsed)
        ? parsed.map((value) => Math.max(1, Math.min(100, Number(value) || 1)))
        : [];
    } catch {
      peaks = [];
    }
    if (!peaks.length) peaks = Array.from({ length: 180 }, () => 18);

    const startMs = Math.max(0, Number.parseInt(container.dataset.startMs || "0", 10) || 0);
    const configuredEndMs = Math.max(0, Number.parseInt(container.dataset.endMs || "0", 10) || 0);
    const storedDurationMs = Math.max(0, Number.parseInt(container.dataset.durationMs || "0", 10) || 0);
    let stoppingAtEnd = false;

    function getDurationMs() {
      if (Number.isFinite(audio.duration) && audio.duration > 0) return audio.duration * 1000;
      return storedDurationMs;
    }

    function getEndMs() {
      const durationMs = getDurationMs();
      if (configuredEndMs > 0) {
        return durationMs > 0 ? Math.min(configuredEndMs, durationMs) : configuredEndMs;
      }
      return durationMs;
    }

    function clipDurationMs() {
      const endMs = getEndMs();
      return Math.max(1, (endMs > startMs ? endMs : getDurationMs()) - startMs);
    }

    function hymnPositionMs() {
      return Math.max(0, Math.min(clipDurationMs(), audio.currentTime * 1000 - startMs));
    }

    function fullProgressRatio() {
      const durationMs = Math.max(1, getDurationMs());
      return Math.max(0, Math.min(1, (audio.currentTime * 1000) / durationMs));
    }

    const adapter = {
      kind: "audio",
      element: container,
      startMs,
      endMs: configuredEndMs,
      play() {
        const endMs = getEndMs();
        const positionMs = audio.currentTime * 1000;
        if (
          positionMs < startMs - 100 ||
          (endMs > startMs && positionMs >= endMs - 100)
        ) {
          audio.currentTime = startMs / 1000;
        }
        const promise = audio.play();
        if (promise?.catch) promise.catch(() => {});
      },
      pause() {
        audio.pause();
      },
      seekTo(ms) {
        const target = boundedSeekTarget(ms, startMs, getEndMs(), getDurationMs());
        audio.currentTime = target / 1000;
        updatePlayer();
        return target;
      },
    };
    adapters.push(adapter);

    function drawWave() {
      const rect = seekSurface.getBoundingClientRect();
      const width = Math.max(1, Math.round(rect.width));
      const height = Math.max(44, Math.round(rect.height));
      const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const style = getComputedStyle(container);
      const baseColor = style.getPropertyValue("--wave-base").trim() || "#b8b8b8";
      const playedColor = style.getPropertyValue("--wave-played").trim() || "#ff5500";
      const outsideColor = style.getPropertyValue("--wave-outside").trim() || "#dedede";
      const durationMs = Math.max(1, getDurationMs());
      const endMs = getEndMs();
      const currentMs = audio.currentTime * 1000;
      const center = height / 2;
      const maxHalf = Math.max(9, height * 0.43);
      const targetBars = Math.max(70, Math.floor(width / 2.5));
      const barCount = Math.min(peaks.length, targetBars);
      const step = width / barCount;
      const barWidth = Math.max(1, Math.min(2, step * 0.58));

      for (let i = 0; i < barCount; i += 1) {
        const sourceIndex = Math.min(peaks.length - 1, Math.floor((i / barCount) * peaks.length));
        const peak = peaks[sourceIndex] / 100;
        const half = Math.max(2, peak * maxHalf);
        const x = i * step + (step - barWidth) / 2;
        const absoluteMs = ((i + 0.5) / barCount) * durationMs;
        const outside = absoluteMs < startMs || (endMs > startMs && absoluteMs > endMs);

        if (outside) ctx.fillStyle = outsideColor;
        else if (absoluteMs <= currentMs && currentMs >= startMs) ctx.fillStyle = playedColor;
        else ctx.fillStyle = baseColor;

        ctx.fillRect(x, center - half, barWidth, half * 2);
      }

      if (playhead) playhead.style.left = `${fullProgressRatio() * 100}%`;
      const clipRatio = Math.max(0, Math.min(1, hymnPositionMs() / clipDurationMs()));
      seekSurface.setAttribute("aria-valuenow", String(Math.round(clipRatio * 100)));
    }

    function updatePlayer() {
      if (currentText) currentText.textContent = formatAudioTime(hymnPositionMs() / 1000);
      if (durationText) durationText.textContent = formatAudioTime(clipDurationMs() / 1000);
      container.classList.toggle("is-playing", !audio.paused && !audio.ended);
      button.setAttribute(
        "aria-label",
        `${audio.paused ? "Play" : "Pause"} ${container.dataset.label || "recording"}`,
      );
      drawWave();
    }

    function seekFromRatio(ratio) {
      const clamped = Math.max(0, Math.min(1, ratio));
      const fullDuration = getDurationMs();
      if (!fullDuration) return;
      adapter.seekTo(clamped * fullDuration);
      activePlayer = adapter;
    }

    function stopAtDefinedEnd() {
      const endMs = getEndMs();
      if (!endMs || stoppingAtEnd) return;
      stoppingAtEnd = true;
      audio.pause();
      try {
        audio.currentTime = endMs / 1000;
      } catch {}
      updatePlayer();
      adapter.onProgress?.(Math.max(0, endMs - startMs));
      adapter.onFinish?.();
      stoppingAtEnd = false;
    }

    button.addEventListener("click", () => {
      activePlayer = adapter;
      if (audio.paused || audio.ended) {
        pauseOthers(adapter);
        adapter.play();
      } else {
        adapter.pause();
      }
    });

    seekSurface.addEventListener("pointerdown", (event) => {
      const rect = seekSurface.getBoundingClientRect();
      if (!rect.width) return;
      seekFromRatio((event.clientX - rect.left) / rect.width);
    });

    seekSurface.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End", "Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "ArrowLeft") adapter.seekTo(audio.currentTime * 1000 - 5000);
      else if (event.key === "ArrowRight") adapter.seekTo(audio.currentTime * 1000 + 5000);
      else if (event.key === "Home") adapter.seekTo(startMs);
      else if (event.key === "End") adapter.seekTo(getEndMs() || getDurationMs());
      else if (audio.paused) adapter.play();
      else adapter.pause();
      activePlayer = adapter;
    });

    audio.addEventListener("loadedmetadata", () => {
      const endMs = getEndMs();
      if (
        audio.currentTime * 1000 < startMs - 100 ||
        (endMs > startMs && audio.currentTime * 1000 > endMs)
      ) {
        audio.currentTime = Math.min(startMs / 1000, Math.max(0, audio.duration - 0.05));
      }
      updatePlayer();
    });
    audio.addEventListener("durationchange", updatePlayer);
    audio.addEventListener("timeupdate", () => {
      activePlayer = adapter;
      const positionMs = audio.currentTime * 1000;
      const endMs = getEndMs();
      if (endMs > startMs && positionMs >= endMs - 40 && !audio.paused) {
        stopAtDefinedEnd();
        return;
      }
      if (positionMs < startMs - 100) {
        audio.currentTime = startMs / 1000;
        return;
      }
      updatePlayer();
      adapter.onProgress?.(hymnPositionMs());
    });
    audio.addEventListener("play", () => {
      activePlayer = adapter;
      pauseOthers(adapter);
      const endMs = getEndMs();
      const positionMs = audio.currentTime * 1000;
      if (
        positionMs < startMs - 100 ||
        (endMs > startMs && positionMs >= endMs - 100)
      ) {
        audio.currentTime = startMs / 1000;
      }
      updatePlayer();
    });
    audio.addEventListener("pause", updatePlayer);
    audio.addEventListener("ended", () => {
      updatePlayer();
      adapter.onFinish?.();
    });
    audio.addEventListener("error", () => container.classList.add("has-error"));

    if (window.ResizeObserver) {
      const observer = new ResizeObserver(drawWave);
      observer.observe(seekSurface);
    } else {
      window.addEventListener("resize", drawWave, { passive: true });
    }
    updatePlayer();
  });

  // ---------------- SoundCloud players ----------------
  const frames = Array.from(document.querySelectorAll("iframe.sc-player"));
  if (frames.length && window.SC?.Widget) {
    frames.forEach((frame) => {
      const widget = window.SC.Widget(frame);
      const startMs = Math.max(0, Number.parseInt(frame.dataset.startMs || "0", 10) || 0);
      const configuredEndMs = Math.max(0, Number.parseInt(frame.dataset.endMs || "0", 10) || 0);
      let durationMs = 0;
      let stoppingAtEnd = false;
      let correctingSeek = false;

      function getEndMs() {
        if (configuredEndMs > 0) {
          return durationMs > 0 ? Math.min(configuredEndMs, durationMs) : configuredEndMs;
        }
        return durationMs;
      }

      function correctUserSeek(position) {
        if (correctingSeek) return;
        const endMs = getEndMs();
        const current = Math.max(0, Number(position) || 0);
        if (current < startMs - 150 || (endMs > startMs && current > endMs + 150)) {
          correctingSeek = true;
          widget.seekTo(startMs);
          window.setTimeout(() => { correctingSeek = false; }, 120);
        }
      }

      function stopAtDefinedEnd() {
        const endMs = getEndMs();
        if (!endMs || stoppingAtEnd) return;
        stoppingAtEnd = true;
        widget.pause();
        widget.seekTo(endMs);
        adapter.onProgress?.(Math.max(0, endMs - startMs));
        adapter.onFinish?.();
        window.setTimeout(() => { stoppingAtEnd = false; }, 120);
      }

      const adapter = {
        kind: "soundcloud",
        element: frame,
        startMs,
        endMs: configuredEndMs,
        play() {
          widget.getPosition((position) => {
            const endMs = getEndMs();
            const current = Math.max(0, Number(position) || 0);
            if (
              current < startMs - 150 ||
              (endMs > startMs && current >= endMs - 100)
            ) {
              widget.seekTo(startMs);
            }
            widget.play();
          });
        },
        pause() { widget.pause(); },
        seekTo(ms) {
          const target = boundedSeekTarget(ms, startMs, getEndMs(), durationMs);
          widget.seekTo(target);
          return target;
        },
      };
      adapters.push(adapter);

      widget.bind(window.SC.Widget.Events.READY, () => {
        widget.getDuration((value) => {
          durationMs = Math.max(0, Number(value) || 0);
        });
        if (startMs > 0) widget.seekTo(startMs);
      });
      widget.bind(window.SC.Widget.Events.PLAY, () => {
        activePlayer = adapter;
        pauseOthers(adapter);
        widget.getPosition((position) => {
          const endMs = getEndMs();
          const current = Math.max(0, Number(position) || 0);
          if (
            current < startMs - 150 ||
            (endMs > startMs && current >= endMs - 100)
          ) {
            widget.seekTo(startMs);
          }
        });
      });
      widget.bind(window.SC.Widget.Events.SEEK, (event) => {
        correctUserSeek(event?.currentPosition);
      });
      widget.bind(window.SC.Widget.Events.PLAY_PROGRESS, (event) => {
        activePlayer = adapter;
        const current = Math.max(0, Number(event.currentPosition) || 0);
        const endMs = getEndMs();

        if (current < startMs - 150) {
          correctUserSeek(current);
          return;
        }
        if (endMs > startMs && current >= endMs - 80) {
          stopAtDefinedEnd();
          return;
        }
        adapter.onProgress?.(Math.max(0, current - startMs));
      });
      widget.bind(window.SC.Widget.Events.FINISH, () => adapter.onFinish?.());
    });
  }

  if (!adapters.length) return;

  // Preserve the same default order the user sees on the page, even when
  // SoundCloud and self-hosted recordings are mixed together.
  adapters.sort((a, b) => {
    if (a.element === b.element) return 0;
    const relation = a.element.compareDocumentPosition(b.element);
    return relation & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  });
  activePlayer = adapters[0];

  // ---------------- Shared lyric synchronization ----------------
  const table = document.getElementById("lyricsTable");
  if (!table) return;
  const rows = Array.from(table.querySelectorAll("tr.lyric-row"));
  if (!rows.length) return;

  const starts = rows.map((row) => Number.parseInt(row.dataset.startMs || "0", 10) || 0);
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

  function finishLyrics() {
    if (activeRow >= 0) rows[activeRow].classList.remove("active");
    activeRow = -1;
  }

  adapters.forEach((adapter) => {
    adapter.onProgress = (hymnPositionMs) => activate(findRow(hymnPositionMs), true);
    adapter.onFinish = finishLyrics;
  });

  rows.forEach((row, index) => {
    const seek = () => {
      if (!isLyricSeekEnabled()) return;
      const player = activePlayer || adapters[0];
      if (!player) return;
      const requestedMs = player.startMs + starts[index];
      const actualMs = player.seekTo(requestedMs);
      pauseOthers(player);
      player.play();
      activePlayer = player;
      activate(findRow(Math.max(0, actualMs - player.startMs)), false);
    };
    row.addEventListener("click", seek);
    row.addEventListener("keydown", (event) => {
      if (!isLyricSeekEnabled()) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        seek();
      }
    });
  });
}

const STATUS = {
  healthy: "норма",
  low: "низкий хэшрейт",
  down: "нет хэшрейта",
  rebooting: "ребут",
  cooldown: "ожидание",
  unknown: "нет данных",
  sleeping: "сон",
};

const state = {
  url: localStorage.getItem("coreUrl") || location.origin,
  token: localStorage.getItem("apiToken") || "",
};

const els = {
  login: document.getElementById("login"),
  dashboard: document.getElementById("dashboard"),
  coreUrl: document.getElementById("coreUrl"),
  apiToken: document.getElementById("apiToken"),
  loginError: document.getElementById("loginError"),
  miners: document.getElementById("miners"),
  events: document.getElementById("events"),
  totalHs: document.getElementById("totalHs"),
  onlineCount: document.getElementById("onlineCount"),
  autoState: document.getElementById("autoState"),
  autoToggle: document.getElementById("autoToggle"),
  title: document.getElementById("title"),
  minersCount: document.getElementById("minersCount"),
  eventsCount: document.getElementById("eventsCount"),
};

els.coreUrl.value = state.url;
els.apiToken.value = state.token;

function headers() {
  return { "Content-Type": "application/json", "X-API-Token": state.token };
}

async function api(path, options = {}) {
  const response = await fetch(`${state.url}${path}`, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  if (response.status === 401) throw new Error("Неверный API токен");
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch (error) {
      detail = "";
    }
    throw new Error(detail || `Ошибка ядра: ${response.status}`);
  }
  return response.json();
}

function showLogin(error = "") {
  els.login.classList.remove("hidden");
  els.dashboard.classList.add("hidden");
  els.loginError.textContent = error;
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.panel !== name);
  });
}

function fmtTime(value) {
  if (!value) return "ещё не было";
  const date = new Date(value);
  return date.toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

function renderMiners(payload) {
  els.title.textContent = payload.location_ip || "УВЫ control";
  const miners = payload.miners || [];
  const total = miners.reduce((sum, miner) => sum + (miner.hashrate_ths || 0), 0);
  const online = miners.filter((miner) => miner.online).length;
  els.totalHs.textContent = `${total.toFixed(1)} TH/s`;
  els.onlineCount.textContent = `${online} / ${miners.length}`;
  els.minersCount.textContent = String(miners.length);
  els.autoState.textContent = payload.auto_reboot_enabled ? "вкл" : "выкл";
  els.autoState.classList.toggle("on", Boolean(payload.auto_reboot_enabled));
  els.autoState.classList.toggle("off", !payload.auto_reboot_enabled);
  els.autoToggle.checked = Boolean(payload.auto_reboot_enabled);
  els.miners.innerHTML = miners.map((miner, index) => {
    const hs = miner.hashrate_ths == null ? "—" : miner.hashrate_ths.toFixed(1);
    const temp = miner.temperature_c == null ? "" : `<span class="temp">${Math.round(miner.temperature_c)}°C</span>`;
    const expected = Number(miner.expected_ths) || 0;
    const pct = miner.hashrate_ths == null || !expected
      ? 0
      : Math.max(0, Math.min(100, (miner.hashrate_ths / expected) * 100));
    const pctLabel = miner.hashrate_ths == null || !expected ? "—" : `${Math.round(pct)}%`;
    const norm = expected ? `<br />норма ${expected} TH/s` : "";
    return `
      <article class="miner ${miner.status}" style="--d:${index}">
        <div class="row">
          <div>
            <div class="name">${miner.name}</div>
            <div class="sub">${miner.firmware} · порт ${miner.port}</div>
          </div>
          <span class="badge ${miner.status}">${STATUS[miner.status] || miner.status}</span>
        </div>
        <div class="hash-line">
          <div class="hash">${hs}<span class="hash-unit">TH/s</span></div>
          ${temp}
        </div>
        <div class="meter-row">
          <div class="meter"><span style="width:${pct}%"></span></div>
          <span class="meter-label">${pctLabel}</span>
        </div>
        <div class="meta">
          последний пинг: ${fmtTime(miner.last_ping_at)}${norm}
          ${miner.last_error ? `<br /><span class="err">ошибка: ${miner.last_error}</span>` : ""}
        </div>
        <button class="reboot" data-id="${miner.id}" type="button">Перезагрузить</button>
      </article>
    `;
  }).join("") || `<div class="empty">Асиков в списке нет</div>`;
}

function renderEvents(payload) {
  const events = payload.events || [];
  els.eventsCount.textContent = String(events.length);
  els.events.innerHTML = events.slice(0, 20).map((event) => `
    <div>
      <time>${fmtTime(event.ts)}</time>
      ${event.message}
    </div>
  `).join("") || `<div>Событий пока нет</div>`;
}

async function loadAll() {
  const [miners, events, settings] = await Promise.all([
    api("/api/miners"),
    api("/api/events"),
    api("/api/settings"),
  ]);
  els.login.classList.add("hidden");
  els.dashboard.classList.remove("hidden");
  renderMiners(miners);
  renderEvents(events);
  renderSleep(settings, miners);
  renderPoolChoices(miners);
  renderPresets(settings.pool_presets || []);
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

document.getElementById("saveLogin").onclick = async () => {
  state.url = els.coreUrl.value.replace(/\/$/, "") || location.origin;
  state.token = els.apiToken.value.trim();
  localStorage.setItem("coreUrl", state.url);
  localStorage.setItem("apiToken", state.token);
  try {
    await loadAll();
  } catch (error) {
    showLogin(error.message);
  }
};

document.getElementById("refreshBtn").onclick = async () => {
  const button = document.getElementById("refreshBtn");
  button.classList.add("busy");
  button.disabled = true;
  try {
    await api("/api/miners/refresh", { method: "POST" });
    await loadAll();
  } catch (error) {
    showLogin(error.message);
  } finally {
    button.classList.remove("busy");
    button.disabled = false;
  }
};

document.getElementById("logoutBtn").onclick = () => {
  showLogin("");
};

els.autoToggle.onchange = async () => {
  await api("/api/settings", {
    method: "PATCH",
    body: JSON.stringify({ auto_reboot_enabled: els.autoToggle.checked }),
  });
  await loadAll();
};

els.miners.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-id]");
  if (!button) return;
  if (!confirm("Перезагрузить этот асик?")) return;
  button.disabled = true;
  button.textContent = "Отправляю ребут...";
  try {
    await api(`/api/miners/${button.dataset.id}/reboot`, { method: "POST" });
    await loadAll();
  } catch (error) {
    alert(error.message);
  }
});

if (state.token) {
  loadAll().catch((error) => showLogin(error.message));
} else {
  showLogin("");
}

function setMessage(node, text, ok) {
  node.textContent = text || "";
  node.classList.toggle("ok", Boolean(ok) && Boolean(text));
  node.classList.toggle("bad", ok === false && Boolean(text));
}

function renderSleep(settings, minersPayload) {
  const minutes = Math.max(1, Math.round((settings.poll_interval_seconds || 600) / 60));
  document.getElementById("pollMinutes").textContent = String(minutes);
  const enabled = document.getElementById("sleepEnabled");
  const start = document.getElementById("sleepStart");
  const end = document.getElementById("sleepEnd");
  if (document.activeElement !== enabled) enabled.checked = Boolean(settings.sleep_enabled);
  if (document.activeElement !== start) start.value = (settings.sleep_start || "23:00").slice(0, 5);
  if (document.activeElement !== end) end.value = (settings.sleep_end || "07:00").slice(0, 5);
  const applied = settings.sleep_applied || {};
  const miners = minersPayload.miners || [];
  document.getElementById("sleepMiners").innerHTML = miners.map((miner) => {
    const asleep = Boolean(applied[miner.id]);
    return `
      <article class="miner ${asleep ? "sleeping" : miner.status}">
        <div class="row">
          <div>
            <div class="name">${miner.name}</div>
            <div class="sub">${miner.firmware} · порт ${miner.port}</div>
          </div>
          <span class="badge ${asleep ? "sleeping" : miner.status}">${asleep ? "сон" : (STATUS[miner.status] || miner.status)}</span>
        </div>
        <div class="sleep-actions">
          <button type="button" data-sleep="1" data-id="${miner.id}">Усыпить</button>
          <button type="button" data-sleep="0" data-id="${miner.id}">Разбудить</button>
        </div>
      </article>
    `;
  }).join("") || `<div class="empty">Асиков в списке нет</div>`;
}

function renderPoolChoices(minersPayload) {
  const select = document.getElementById("poolMiner");
  const current = select.value;
  const miners = minersPayload.miners || [];
  select.innerHTML = miners.map((miner) => `<option value="${miner.id}">${miner.name}</option>`).join("");
  if (current && miners.some((miner) => miner.id === current)) select.value = current;
}

function poolValues() {
  return [...document.querySelectorAll(".pool-slot")].map((slot) => ({
    url: slot.querySelector(".pool-url").value.trim(),
    user: slot.querySelector(".pool-user").value.trim(),
    password: slot.querySelector(".pool-pass").value,
  }));
}

function fillPools(pools) {
  const slots = document.querySelectorAll(".pool-slot");
  slots.forEach((slot, index) => {
    const pool = pools[index] || {};
    slot.querySelector(".pool-url").value = pool.url || "";
    slot.querySelector(".pool-user").value = pool.user || "";
    slot.querySelector(".pool-pass").value = pool.password || pool.pass || "";
  });
}

function renderPresets(presets) {
  document.getElementById("presets").innerHTML = presets.map((preset) => `
    <article>
      <div class="row">
        <div>
          <div class="name">${preset.name}</div>
          <div class="sub">${(preset.pools || []).map((pool) => pool.url).filter(Boolean).join(" · ") || "пустые слоты"}</div>
        </div>
      </div>
      <div class="sleep-actions">
        <button type="button" data-preset="${preset.id}" data-preset-action="fill">Подставить</button>
        <button type="button" data-preset="${preset.id}" data-preset-action="delete">Удалить</button>
      </div>
    </article>
  `).join("") || `<div class="empty">Сохранённых наборов пока нет</div>`;
  document.getElementById("presets").dataset.items = JSON.stringify(presets);
}

function resultText(payload) {
  const rows = payload.results || [payload];
  return rows.map((item) => `${item.name}: ${item.ok ? "готово" : (item.detail || "ошибка")}`).join("\n");
}

document.getElementById("saveSleep").onclick = async () => {
  const message = document.getElementById("sleepMsg");
  setMessage(message, "Сохраняю...", null);
  try {
    await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({
        sleep_enabled: document.getElementById("sleepEnabled").checked,
        sleep_start: document.getElementById("sleepStart").value,
        sleep_end: document.getElementById("sleepEnd").value,
      }),
    });
    await loadAll();
    setMessage(message, "Расписание сохранено", true);
  } catch (error) {
    setMessage(message, error.message, false);
  }
};

async function commandSleep(sleep, minerIds) {
  const message = document.getElementById("sleepMsg");
  setMessage(message, sleep ? "Усыпляю..." : "Бужу...", null);
  try {
    const payload = await api("/api/sleep", {
      method: "POST",
      body: JSON.stringify({ sleep, miner_ids: minerIds }),
    });
    await loadAll();
    const rows = payload.results || [];
    const failed = rows.some((item) => !item.ok);
    setMessage(message, resultText(payload), !failed);
  } catch (error) {
    setMessage(message, error.message, false);
  }
}

document.getElementById("sleepAll").onclick = () => {
  if (!confirm("Усыпить все асики?")) return;
  commandSleep(true, []);
};

document.getElementById("wakeAll").onclick = () => {
  if (!confirm("Разбудить все асики?")) return;
  commandSleep(false, []);
};

document.getElementById("sleepMiners").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sleep]");
  if (!button) return;
  const sleep = button.dataset.sleep === "1";
  commandSleep(sleep, [button.dataset.id]);
});

document.getElementById("loadPools").onclick = async () => {
  const message = document.getElementById("poolMsg");
  const minerId = document.getElementById("poolMiner").value;
  if (!minerId) return;
  setMessage(message, "Читаю пулы...", null);
  try {
    const payload = await api(`/api/miners/${minerId}/pools`);
    fillPools(payload.pools || []);
    const mode = payload.sleeping == null ? "" : payload.sleeping ? " Сейчас асик спит." : " Сейчас асик работает.";
    setMessage(message, `Пулы прочитаны.${mode}`, true);
  } catch (error) {
    setMessage(message, error.message, false);
  }
};

async function applyPools(minerIds) {
  const message = document.getElementById("poolMsg");
  setMessage(message, "Записываю пулы...", null);
  try {
    const payload = await api("/api/pools/apply", {
      method: "POST",
      body: JSON.stringify({
        pools: poolValues(),
        miner_ids: minerIds,
        keep_worker: document.getElementById("keepWorker").checked,
      }),
    });
    const rows = payload.results || [];
    setMessage(message, resultText(payload), rows.every((item) => item.ok));
  } catch (error) {
    setMessage(message, error.message, false);
  }
}

document.getElementById("applyPoolOne").onclick = () => {
  const minerId = document.getElementById("poolMiner").value;
  if (!minerId) return;
  if (!confirm("Записать пулы на выбранный асик?")) return;
  applyPools([minerId]);
};

document.getElementById("applyPoolAll").onclick = () => {
  if (!confirm("Записать эти пулы на все асики?")) return;
  applyPools([]);
};

document.getElementById("savePreset").onclick = async () => {
  const message = document.getElementById("poolMsg");
  const name = document.getElementById("presetName").value.trim();
  if (!name) {
    setMessage(message, "Нужно название набора", false);
    return;
  }
  try {
    await api("/api/presets", {
      method: "POST",
      body: JSON.stringify({ name, pools: poolValues() }),
    });
    document.getElementById("presetName").value = "";
    await loadAll();
    setMessage(message, "Набор сохранён", true);
  } catch (error) {
    setMessage(message, error.message, false);
  }
};

document.getElementById("presets").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-preset]");
  if (!button) return;
  const presets = JSON.parse(document.getElementById("presets").dataset.items || "[]");
  const preset = presets.find((item) => item.id === button.dataset.preset);
  if (button.dataset.presetAction === "fill" && preset) {
    fillPools(preset.pools || []);
    setMessage(document.getElementById("poolMsg"), `Подставлен набор «${preset.name}»`, true);
    return;
  }
  if (button.dataset.presetAction === "delete") {
    try {
      await api(`/api/presets/${button.dataset.preset}`, { method: "DELETE" });
      await loadAll();
    } catch (error) {
      setMessage(document.getElementById("poolMsg"), error.message, false);
    }
  }
});

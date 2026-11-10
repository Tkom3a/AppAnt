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
  miners: [],
  events: [],
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
  state.miners = miners;
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
      <article class="miner ${miner.status}" style="--d:${index}" data-id="${htmlEscape(miner.id)}">
        <div class="row">
          <div>
            <div class="name">${htmlEscape(miner.name)}</div>
            <div class="sub">${htmlEscape(miner.firmware)} · порт ${miner.port}</div>
            <div class="sub pool-now">${htmlEscape(activePoolText(miner))}</div>
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
          ${miner.last_error ? `<br /><span class="err">ошибка: ${htmlEscape(miner.last_error)}</span>` : ""}
        </div>
        <button class="reboot" data-open="${htmlEscape(miner.id)}" type="button">Открыть</button>
        <button class="reboot" data-id="${htmlEscape(miner.id)}" type="button">Перезагрузить</button>
      </article>
    `;
  }).join("") || `<div class="empty">Асиков в списке нет</div>`;
  if (openMinerId) fillMinerCard(openMinerId);
}

function activePoolText(miner) {
  const pools = miner.pools || [];
  const current = pools.find((pool) => pool.active && pool.url) || pools.find((pool) => pool.url);
  if (!current) return "пул ещё не известен";
  const url = String(current.url).replace(/^stratum\+\w+:\/\//, "");
  return current.user ? `${url} · ${current.user}` : url;
}

let openMinerId = "";

function fillMinerCard(minerId) {
  const miner = state.miners.find((item) => item.id === minerId);
  const card = document.getElementById("minerDetail");
  if (!miner || !card) return;
  openMinerId = minerId;
  card.classList.remove("hidden");
  document.getElementById("minerDetailTitle").textContent = miner.name;
  document.getElementById("minerDetailSub").textContent = `${firmwareLabel(miner.firmware)} · порт ${miner.port} · ${STATUS[miner.status] || miner.status}`;
  const temps = miner.temperatures || [];
  document.getElementById("minerDetailTemps").innerHTML = temps.map((item) => `
    <article><div class="name">${htmlEscape(item.label)}</div><div class="hash">${Math.round(item.value)}°C</div></article>
  `).join("") || `<div class="empty">Температур ещё нет</div>`;
  const fans = miner.fans_rpm || [];
  document.getElementById("minerDetailFans").innerHTML = fans.map((rpm, index) => `
    <article><div class="name">Вентилятор ${index + 1}</div><div class="hash">${rpm}</div><div class="sub">об/мин</div></article>
  `).join("") || `<div class="empty">Оборотов ещё нет</div>`;
  const pools = miner.pools || [];
  document.getElementById("minerDetailPools").innerHTML = pools.map((pool) => `
    <article>
      <div class="name">${pool.active ? "подключён" : (htmlEscape(pool.status) || "запасной")}</div>
      <div class="sub">${htmlEscape(String(pool.url || "").replace(/^stratum\+\w+:\/\//, ""))}</div>
      <div class="sub">${htmlEscape(pool.user || "")}</div>
    </article>
  `).join("") || `<div class="empty">Пулы ещё не прочитаны</div>`;
  const events = state.events.filter((event) => event.miner_id === minerId);
  document.getElementById("minerDetailEvents").innerHTML = events.map((event) => `
    <div><time>${fmtTime(event.ts)}</time>${htmlEscape(event.message)}</div>
  `).join("") || `<div class="empty">Событий по этому асику нет</div>`;
}

function closeMinerCard() {
  openMinerId = "";
  document.getElementById("minerDetail").classList.add("hidden");
}

function renderEvents(payload) {
  const events = payload.events || [];
  state.events = events;
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
  await loadPoolFile();
}

document.getElementById("minerDetailClose").onclick = closeMinerCard;
document.getElementById("minerDetail").addEventListener("click", (event) => {
  if (event.target.id === "minerDetail") closeMinerCard();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMinerCard();
});

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
  const opener = event.target.closest("button[data-open]");
  if (opener) {
    fillMinerCard(opener.dataset.open);
    return;
  }
  const button = event.target.closest("button[data-id]");
  if (!button) {
    const article = event.target.closest("article[data-id]");
    if (article) fillMinerCard(article.dataset.id);
    return;
  }
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

let minerCatalog = [];
const selectedMiners = new Set();
let minerSelectionReady = false;

function walletWithWorker(wallet, worker, known) {
  wallet = String(wallet || "").trim();
  const suffix = String(worker || "").trim().replace(/^\.+/, "");
  if (!wallet || !suffix) return wallet;
  const dot = wallet.lastIndexOf(".");
  if (dot > 0) {
    const tail = wallet.slice(dot + 1);
    const knownSet = new Set((known || []).map((item) => String(item || "").trim().replace(/^\.+/, "")).filter(Boolean));
    if (knownSet.has(tail) || /^\d+$/.test(tail)) wallet = wallet.slice(0, dot);
  }
  return `${wallet}.${suffix}`;
}

function firmwareLabel(firmware) {
  if (firmware === "vnish") return "Vnish · пароль, unlock";
  if (firmware === "antminer") return "Antminer · логин, digest";
  return firmware || "";
}

function knownWorkers() {
  return minerCatalog.map((miner) => miner.worker);
}

function rememberMiners(list) {
  const byId = new Map(minerCatalog.map((miner) => [miner.id, miner]));
  (list || []).forEach((miner) => {
    if (!miner || !miner.id) return;
    byId.set(miner.id, { ...byId.get(miner.id), ...miner });
  });
  minerCatalog = [...byId.values()];
  if (!minerSelectionReady && minerCatalog.length) {
    minerCatalog.forEach((miner) => selectedMiners.add(miner.id));
    minerSelectionReady = true;
  }
  [...selectedMiners].forEach((id) => {
    if (!minerCatalog.some((miner) => miner.id === id)) selectedMiners.delete(id);
  });
  renderMinerMenu();
  refreshWalletPreview();
}

function htmlEscape(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}
function baseWallet() {
  const pools = [...document.querySelectorAll("#poolFileList .pool-card-drag")];
  const fromCard = pools[0]?.querySelector("[data-wallet]")?.dataset.wallet || "";
  if (fromCard) return fromCard;
  const input = document.querySelector(".pool-user");
  return input ? input.value.trim() : "";
}

function renderMinerMenu() {
  const button = document.getElementById("minerMenuButton");
  const list = document.getElementById("minerMenuList");
  if (!button || !list) return;
  const selected = minerCatalog.filter((miner) => selectedMiners.has(miner.id));
  if (!minerCatalog.length) button.textContent = "Асиков нет";
  else if (selected.length === minerCatalog.length) button.textContent = "Все асики";
  else if (!selected.length) button.textContent = "Асики не выбраны";
  else button.textContent = selected.map((miner) => miner.name).join(", ");
  const wallet = baseWallet();
  const known = knownWorkers();
  list.innerHTML = minerCatalog.map((miner) => `
    <label class="check-line miner-row">
      <span>
        <input type="checkbox" data-miner="${htmlEscape(miner.id)}" ${selectedMiners.has(miner.id) ? "checked" : ""} />
        ${htmlEscape(miner.name)}
        <small class="fw">${htmlEscape(firmwareLabel(miner.firmware))}</small>
      </span>
      <span class="wallet">${htmlEscape(walletWithWorker(wallet, miner.worker, known) || "без кошелька")}</span>
    </label>
  `).join("") || `<div class="empty">Асиков в miners.json нет</div>`;
}

function refreshWalletPreview() {
  const known = knownWorkers();
  const selected = minerCatalog.filter((miner) => selectedMiners.has(miner.id));
  document.querySelectorAll(".pool-slot").forEach((slot) => {
    const input = slot.querySelector(".pool-user");
    if (!input) return;
    let preview = slot.querySelector(".wallet-preview");
    if (!preview) {
      preview = document.createElement("p");
      preview.className = "wallet-preview";
      input.insertAdjacentElement("afterend", preview);
    }
    if (!selected.length) {
      preview.textContent = "Отметьте асики в меню";
      return;
    }
    preview.textContent = selected
      .map((miner) => `${miner.name} · ${firmwareLabel(miner.firmware)}: ${walletWithWorker(input.value, miner.worker, known)}`)
      .join(" · ");
  });
}

function checkedMinerIds() {
  return minerCatalog.filter((miner) => selectedMiners.has(miner.id)).map((miner) => miner.id);
}

function renderPoolFile(payload) {
  const list = document.getElementById("poolFileList");
  const workers = document.getElementById("poolWorkerList");
  if (!payload.ok) {
    list.innerHTML = `<div class="empty">${payload.detail || "Файл пулов не прочитан"}</div>`;
    if (workers) workers.innerHTML = "";
    rememberMiners([]);
    return;
  }
  list.innerHTML = (payload.pools || []).map((pool, index) => `
    <article class="pool-card-drag" data-pool-index="${index}">
      <span class="drag-handle" aria-hidden="true">⋮⋮</span>
      <div>
        <div class="name">${index + 1}. ${pool.name}</div>
        <div class="sub" data-wallet="${htmlEscape(pool.user)}">${htmlEscape(pool.url)}<br />кошелёк ${htmlEscape(pool.user)}</div>
      </div>
      <div class="pool-move">
        <button type="button" data-move="-1" aria-label="Выше">↑</button>
        <button type="button" data-move="1" aria-label="Ниже">↓</button>
      </div>
    </article>
  `).join("") || `<div class="empty">В pools.json нет пулов</div>`;
  if (workers) {
    workers.innerHTML = (payload.miners || []).map((miner) => `
      <article>
        <div class="row">
          <div class="name">${miner.name}</div>
          <span class="badge">${miner.firmware === "vnish" ? "Vnish" : "Antminer"} · ${miner.worker_user || "без номера"}</span>
        </div>
      </article>
    `).join("");
  }
  rememberMiners(payload.miners || []);
  bindPoolDrag(list);
}

function currentPoolOrder() {
  return [...document.querySelectorAll("#poolFileList .pool-card-drag")]
    .map((card) => Number(card.dataset.poolIndex));
}

async function savePoolOrder(order) {
  const message = document.getElementById("poolFileMsg");
  setMessage(message, "Сохраняю порядок...", null);
  try {
    const payload = await api("/api/pools/order", {
      method: "PUT",
      body: JSON.stringify({ order }),
    });
    renderPoolFile(payload);
    setMessage(message, "Порядок сохранён. Запишите его на отмеченные асики.", true);
  } catch (error) {
    setMessage(message, error.message, false);
    await loadPoolFile();
  }
}

function bindPoolDrag(list) {
  let dragged = null;
  let startOrder = "";
  let frame = 0;
  let lastY = 0;

  function moveCard(card, y) {
    const cards = [...list.querySelectorAll(".pool-card-drag")];
    const index = cards.indexOf(card);
    const previous = cards[index - 1];
    const next = cards[index + 1];
    if (previous) {
      const rect = previous.getBoundingClientRect();
      if (y < rect.top + rect.height / 2) {
        previous.before(card);
        return;
      }
    }
    if (next) {
      const rect = next.getBoundingClientRect();
      if (y > rect.top + rect.height / 2) next.after(card);
    }
  }

  function finish(card) {
    if (dragged !== card) return;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    dragged.classList.remove("dragging");
    const order = currentPoolOrder();
    const changed = order.join(",") !== startOrder;
    dragged = null;
    if (changed) savePoolOrder(order);
  }

  list.querySelectorAll(".pool-card-drag").forEach((card) => {
    const handle = card.querySelector(".drag-handle");
    if (!handle) return;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 && event.pointerType === "mouse") return;
      dragged = card;
      startOrder = currentPoolOrder().join(",");
      card.classList.add("dragging");
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    handle.addEventListener("pointermove", (event) => {
      if (dragged !== card) return;
      lastY = event.clientY;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (dragged === card) moveCard(card, lastY);
      });
    });
    handle.addEventListener("pointerup", () => finish(card));
    handle.addEventListener("pointercancel", () => finish(card));
  });

  list.querySelectorAll("button[data-move]").forEach((button) => {
    button.addEventListener("click", () => {
      const cards = [...list.querySelectorAll(".pool-card-drag")];
      const card = button.closest(".pool-card-drag");
      const index = cards.indexOf(card);
      const next = index + Number(button.dataset.move);
      if (next < 0 || next >= cards.length) return;
      if (next < index) cards[next].before(card);
      else cards[next].after(card);
      savePoolOrder(currentPoolOrder());
    });
  });
}

function renumberSlots() {
  document.querySelectorAll("#poolSlots .pool-slot p").forEach((title, index) => {
    title.textContent = `Пул ${index + 1}`;
  });
}

function bindSlotDrag() {
  const list = document.getElementById("poolSlots");
  if (!list) return;
  let dragged = null;
  let frame = 0;
  let lastY = 0;

  function moveSlot(slot, y) {
    const slots = [...list.querySelectorAll(".pool-slot")];
    const index = slots.indexOf(slot);
    const previous = slots[index - 1];
    const next = slots[index + 1];
    if (previous) {
      const rect = previous.getBoundingClientRect();
      if (y < rect.top + rect.height / 2) {
        previous.before(slot);
        return;
      }
    }
    if (next) {
      const rect = next.getBoundingClientRect();
      if (y > rect.top + rect.height / 2) next.after(slot);
    }
  }

  function finish(slot) {
    if (dragged !== slot) return;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    dragged.classList.remove("dragging");
    dragged = null;
    renumberSlots();
  }

  list.querySelectorAll(".pool-slot").forEach((slot) => {
    slot.addEventListener("pointerdown", (event) => {
      if (event.target.closest("input, textarea, button, select, a")) return;
      if (event.button !== 0 && event.pointerType === "mouse") return;
      dragged = slot;
      slot.classList.add("dragging");
      slot.setPointerCapture(event.pointerId);
    });
    slot.addEventListener("pointermove", (event) => {
      if (dragged !== slot) return;
      lastY = event.clientY;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (dragged === slot) moveSlot(slot, lastY);
      });
    });
    slot.addEventListener("pointerup", () => finish(slot));
    slot.addEventListener("pointercancel", () => finish(slot));
  });
}

bindSlotDrag();

async function loadPoolFile() {
  try {
    renderPoolFile(await api("/api/pools/config"));
  } catch (error) {
    renderPoolFile({ ok: false, detail: error.message, pools: [], miners: [] });
  }
}

function renderPoolChoices(minersPayload) {
  const select = document.getElementById("poolMiner");
  const current = select.value;
  const miners = minersPayload.miners || [];
  rememberMiners(miners);
  select.innerHTML = miners.map((miner) => `<option value="${htmlEscape(miner.id)}">${htmlEscape(miner.name)}</option>`).join("");
  if (miners.some((miner) => miner.id === current)) select.value = current;
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
  refreshWalletPreview();
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
  return rows.map((item) => {
    const via = item.firmware === "vnish" ? "Vnish" : item.firmware === "antminer" ? "Antminer" : "";
    const tail = item.ok ? "готово" : (item.detail || "ошибка");
    return `${item.name}${via ? " · " + via : ""}: ${tail}`;
  }).join("\n");
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

document.getElementById("minerMenuButton").onclick = () => {
  renderMinerMenu();
  document.getElementById("minerMenuPanel").classList.toggle("hidden");
};

document.getElementById("minerMenuAll").onclick = () => {
  minerCatalog.forEach((miner) => selectedMiners.add(miner.id));
  renderMinerMenu();
  refreshWalletPreview();
};

document.getElementById("minerMenuList").addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || !input.dataset.miner) return;
  if (input.checked) selectedMiners.add(input.dataset.miner);
  else selectedMiners.delete(input.dataset.miner);
  renderMinerMenu();
  refreshWalletPreview();
});

document.addEventListener("click", (event) => {
  if (event.target.closest("#minerMenu")) return;
  document.getElementById("minerMenuPanel").classList.add("hidden");
});

document.getElementById("poolSlots").addEventListener("input", refreshWalletPreview);

document.getElementById("applyPoolFile").onclick = async () => {
  const message = document.getElementById("poolFileMsg");
  const ids = checkedMinerIds();
  if (!ids.length) {
    setMessage(message, "Отметьте асики или нажмите «Все»", false);
    return;
  }
  const all = ids.length === minerCatalog.length;
  const question = all
    ? "Записать этот порядок пулов на все асики?"
    : "Записать этот порядок пулов на отмеченные асики?";
  if (!confirm(question)) return;
  setMessage(message, "Записываю пулы из файла...", null);
  try {
    const payload = await api("/api/pools/apply-config", {
      method: "POST",
      body: JSON.stringify({ miner_ids: all ? [] : ids }),
    });
    const rows = payload.results || [];
    setMessage(message, resultText(payload), rows.every((item) => item.ok));
    await loadPoolFile();
  } catch (error) {
    setMessage(message, error.message, false);
  }
};

document.getElementById("loadPools").onclick = async () => {
  const message = document.getElementById("poolMsg");
  const minerId = document.getElementById("poolMiner").value;
  if (!minerId) {
    setMessage(message, "Выберите один асик, чтобы прочитать пулы", false);
    return;
  }
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
        keep_worker: Boolean(document.getElementById("keepWorker")?.checked),
      }),
    });
    const rows = payload.results || [];
    setMessage(message, resultText(payload), rows.every((item) => item.ok));
  } catch (error) {
    setMessage(message, error.message, false);
  }
}

document.getElementById("applyPoolOne").onclick = () => {
  const message = document.getElementById("poolMsg");
  const pools = poolValues();
  if (!pools.some((pool) => pool.url)) {
    setMessage(message, "Впишите адрес хотя бы первого пула", false);
    return;
  }
  if (!confirm("Разослать эти пулы на все асики? К каждому кошельку допишется свой номер воркера.")) return;
  broadcastPools(pools);
};

async function broadcastPools(pools) {
  const message = document.getElementById("poolMsg");
  setMessage(message, "Рассылаю на все асики...", null);
  try {
    const payload = await api("/api/pools/broadcast", {
      method: "POST",
      body: JSON.stringify({ pools }),
    });
    const rows = payload.results || [];
    const saved = payload.saved === false ? " Файл pools.json не записался." : "";
    setMessage(message, `${resultText(payload)}${saved}`, rows.length > 0 && rows.every((item) => item.ok));
  } catch (error) {
    setMessage(message, error.message, false);
  }
}

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

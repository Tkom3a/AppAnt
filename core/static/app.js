const STATUS = {
  healthy: "норма",
  low: "низкий хэшрейт",
  down: "нет хэшрейта",
  rebooting: "ребут",
  cooldown: "ожидание",
  unknown: "нет данных",
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
  autoState: document.getElementById("autoState"),
  autoToggle: document.getElementById("autoToggle"),
  title: document.getElementById("title"),
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
  if (!response.ok) throw new Error(`Ошибка ядра: ${response.status}`);
  return response.json();
}

function showLogin(error = "") {
  els.login.classList.remove("hidden");
  els.dashboard.classList.add("hidden");
  els.loginError.textContent = error;
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
  els.totalHs.textContent = `${total.toFixed(1)} TH/s`;
  els.autoState.textContent = payload.auto_reboot_enabled ? "вкл" : "выкл";
  els.autoToggle.checked = Boolean(payload.auto_reboot_enabled);
  els.miners.innerHTML = miners.map((miner) => {
    const hs = miner.hashrate_ths == null ? "—" : `${miner.hashrate_ths.toFixed(1)} TH/s`;
    const temp = miner.temperature_c == null ? "" : ` · ${Math.round(miner.temperature_c)}°C`;
    return `
      <article>
        <div class="row">
          <div class="name">${miner.name}</div>
          <span class="badge ${miner.status}">${STATUS[miner.status] || miner.status}</span>
        </div>
        <div class="hash">${hs}</div>
        <div class="meta">
          ${miner.firmware} · порт ${miner.port}${temp}<br />
          последний пинг: ${fmtTime(miner.last_ping_at)}
          ${miner.last_error ? `<br />ошибка: ${miner.last_error}` : ""}
        </div>
        <button class="reboot" data-id="${miner.id}" type="button">Перезагрузить</button>
      </article>
    `;
  }).join("");
}

function renderEvents(payload) {
  const events = payload.events || [];
  els.events.innerHTML = events.slice(0, 20).map((event) => `
    <div>
      <time>${fmtTime(event.ts)}</time>
      ${event.message}
    </div>
  `).join("") || `<div>Событий пока нет</div>`;
}

async function loadAll() {
  const [miners, events] = await Promise.all([api("/api/miners"), api("/api/events")]);
  els.login.classList.add("hidden");
  els.dashboard.classList.remove("hidden");
  renderMiners(miners);
  renderEvents(events);
}

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
  try {
    await api("/api/miners/refresh", { method: "POST" });
    await loadAll();
  } catch (error) {
    showLogin(error.message);
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

// LED kiosk frontend. Vanilla JS on purpose -- one file, no build step,
// easy to tweak directly on the Pi if needed.
//
// Wire protocol reminder: each command is a single 3-letter code plus at
// most one value (number, hex color, or 0/1 toggle) -- never a list of
// params. The backend builds the final "FRM5"-style string; this file just
// collects one value per command and posts { command_id, value }.

let profile = null;

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------- connectivity status ----------
const NETWORK_LABELS = { wifi: "WiFi", lte: "LTE", ethernet: "Ethernet" };

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    document.getElementById("statusDot").classList.toggle("connected", data.mqtt_connected);
    const base = data.mqtt_connected ? "broker conectado" : "broker desconectado";
    const netLabel = NETWORK_LABELS[data.network_type];
    document.getElementById("statusText").textContent = netLabel ? `${base} · ${netLabel}` : base;
  } catch {
    document.getElementById("statusDot").classList.remove("connected");
    document.getElementById("statusText").textContent = "app sin conexión";
  }
}
setInterval(pollStatus, 4000);
pollStatus();

// ---------- single-value field rendering (shared by command sheet + schedule form) ----------
// Returns { el, getValue } or null if the command takes no value at all
// (value_type "none" with no fixed_value -- e.g. INV, AUT, STA).
function renderValueField(cmd) {
  if (cmd.fixed_value !== undefined) {
    const note = document.createElement("p");
    note.className = "meta";
    note.textContent = `Envía ${cmd.id}${cmd.fixed_value} -- no requiere valor.`;
    return { el: note, getValue: () => null }; // caller uses cmd.fixed_value directly, not this
  }

  if (cmd.value_type === "none") return null;

  const wrap = document.createElement("div");
  const label = document.createElement("label");
  label.textContent = cmd.unit ? `valor (${cmd.unit})` : "valor";
  wrap.appendChild(label);

  if (cmd.value_type === "slider") {
    const input = document.createElement("input");
    input.type = "range";
    input.min = cmd.min; input.max = cmd.max;
    input.value = cmd.default ?? cmd.min;
    const readout = document.createElement("span");
    readout.className = "meta";
    readout.textContent = input.value;
    input.addEventListener("input", () => (readout.textContent = input.value));
    wrap.appendChild(input);
    wrap.appendChild(readout);
    return { el: wrap, getValue: () => Number(input.value) };
  }

  if (cmd.value_type === "number") {
    const input = document.createElement("input");
    input.type = "number";
    if (cmd.min !== undefined) input.min = cmd.min;
    if (cmd.max !== undefined) input.max = cmd.max;
    input.value = cmd.default ?? cmd.min ?? 0;
    wrap.appendChild(input);
    if (cmd.note) {
      const note = document.createElement("span");
      note.className = "meta";
      note.textContent = cmd.note;
      wrap.appendChild(note);
    }
    return { el: wrap, getValue: () => Number(input.value) };
  }

  if (cmd.value_type === "hex_color") {
    const input = document.createElement("input");
    input.type = "color";
    input.value = cmd.default ?? "#ffffff";
    wrap.appendChild(input);
    // device wants hex WITHOUT '#' -- backend also strips it defensively,
    // but send it clean from here too
    return { el: wrap, getValue: () => input.value.replace("#", "") };
  }

  if (cmd.value_type === "toggle") {
    const select = document.createElement("select");
    select.innerHTML = `<option value="1">Encendido (1)</option><option value="0">Apagado (0)</option>`;
    wrap.appendChild(select);
    return { el: wrap, getValue: () => Number(select.value) };
  }

  // fallback: plain text
  const input = document.createElement("input");
  input.type = "text";
  wrap.appendChild(input);
  return { el: wrap, getValue: () => input.value };
}

// ---------- commands tab ----------
async function loadProfile() {
  const res = await fetch("/api/commands");
  profile = await res.json();
  const grid = document.getElementById("commandGrid");
  grid.innerHTML = "";
  for (const cmd of profile.commands) {
    const card = document.createElement("div");
    card.className = "command-card";
    card.innerHTML = `<span class="command-glyph">${cmd.id[0]}</span><span>${cmd.label}</span>`;
    card.addEventListener("click", () => openCommandSheet(cmd));
    grid.appendChild(card);
  }
  populateScheduleCommandSelect();
}

function openCommandSheet(cmd) {
  const backdrop = document.getElementById("sheetBackdrop");
  const sheet = document.getElementById("sheet");
  sheet.innerHTML = "";

  const closeRow = document.createElement("div");
  closeRow.className = "close-row";
  closeRow.innerHTML = `<button aria-label="Cerrar">&times;</button>`;
  closeRow.querySelector("button").addEventListener("click", () => backdrop.classList.remove("open"));
  sheet.appendChild(closeRow);

  const h2 = document.createElement("h2");
  h2.textContent = cmd.label;
  sheet.appendChild(h2);

  const code = document.createElement("p");
  code.className = "meta";
  code.textContent = `Código de comando: ${cmd.id}`;
  sheet.appendChild(code);

  const field = renderValueField(cmd);
  if (field) sheet.appendChild(field.el);

  const sendBtn = document.createElement("button");
  sendBtn.className = "primary";
  sendBtn.textContent = "Enviar comando";
  sendBtn.addEventListener("click", async () => {
    if (cmd.confirm && !confirm(cmd.confirm_text || "¿Estás seguro?")) return;

    const value = cmd.fixed_value !== undefined
      ? cmd.fixed_value
      : (field ? field.getValue() : null);

    sendBtn.disabled = true;
    sendBtn.textContent = "Enviando…";
    try {
      const res = await fetch("/api/commands/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command_id: cmd.id, value }),
      });
      sendBtn.textContent = res.ok ? "Enviado" : "Error";
    } catch {
      sendBtn.textContent = "Error";
    }
    setTimeout(() => backdrop.classList.remove("open"), 600);
  });
  sheet.appendChild(sendBtn);

  backdrop.classList.add("open");
}
document.getElementById("sheetBackdrop").addEventListener("click", (e) => {
  if (e.target.id === "sheetBackdrop") e.currentTarget.classList.remove("open");
});

// ---------- log tab ----------
function appendLogLine(msg) {
  const view = document.getElementById("logView");
  const line = document.createElement("div");
  line.className = "log-line";
  const ts = new Date().toLocaleTimeString();
  line.innerHTML = `<span class="ts">${ts}</span><span class="topic">${msg.topic}</span><span class="payload"></span>`;
  line.querySelector(".payload").textContent = msg.payload; // textContent, not innerHTML: payload is untrusted device data
  view.appendChild(line);
  view.scrollTop = view.scrollHeight;
}

async function loadRecentLogs() {
  const res = await fetch("/api/logs?limit=200");
  const logs = await res.json();
  for (const row of logs) {
    appendLogLine({ topic: row.topic, payload: row.payload });
  }
}

function connectLogSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
  ws.onmessage = (evt) => appendLogLine(JSON.parse(evt.data));
  ws.onclose = () => setTimeout(connectLogSocket, 2000); // simple reconnect
}

// ---------- schedule tab ----------
function populateScheduleCommandSelect() {
  const form = document.getElementById("scheduleForm");
  form.innerHTML = "";

  const select = document.createElement("select");
  for (const cmd of profile.commands) {
    const opt = document.createElement("option");
    opt.value = cmd.id;
    opt.textContent = `${cmd.label} (${cmd.id})`;
    select.appendChild(opt);
  }
  form.appendChild(select);

  const fieldContainer = document.createElement("div");
  form.appendChild(fieldContainer);

  let currentField = null;
  function renderField() {
    fieldContainer.innerHTML = "";
    const cmd = profile.commands.find((c) => c.id === select.value);
    currentField = renderValueField(cmd);
    if (currentField) fieldContainer.appendChild(currentField.el);
  }
  select.addEventListener("change", renderField);
  renderField();

  const dtLabel = document.createElement("label");
  dtLabel.textContent = "ejecutar el";
  form.appendChild(dtLabel);
  const dtInput = document.createElement("input");
  dtInput.type = "datetime-local";
  form.appendChild(dtInput);

  const addBtn = document.createElement("button");
  addBtn.className = "primary";
  addBtn.textContent = "Programar comando";
  addBtn.addEventListener("click", async () => {
    if (!dtInput.value) return;
    const cmd = profile.commands.find((c) => c.id === select.value);
    const value = cmd.fixed_value !== undefined
      ? cmd.fixed_value
      : (currentField ? currentField.getValue() : null);
    await fetch("/api/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command_id: select.value, value, run_at: dtInput.value }),
    });
    loadSchedules();
  });
  form.appendChild(addBtn);
}

async function loadSchedules() {
  const res = await fetch("/api/schedule");
  const rows = await res.json();
  const list = document.getElementById("scheduleList");
  list.innerHTML = "";
  for (const row of rows) {
    const item = document.createElement("div");
    item.className = "schedule-item";
    const when = new Date(row.run_at * 1000).toLocaleString();
    item.innerHTML = `
      <div>
        <div>${row.command_id}</div>
        <div class="meta">${when} · ${row.status}</div>
      </div>
      <button class="btn-cancel">Cancelar</button>`;
    item.querySelector(".btn-cancel").addEventListener("click", async () => {
      await fetch(`/api/schedule/${row.id}`, { method: "DELETE" });
      loadSchedules();
    });
    list.appendChild(item);
  }
}

// ---------- boot ----------
loadProfile();
loadRecentLogs().then(connectLogSocket);
loadSchedules();
setInterval(loadSchedules, 15000);

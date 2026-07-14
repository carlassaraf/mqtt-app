// LED kiosk frontend. Vanilla JS on purpose -- one file, no build step,
// easy to tweak directly on the Pi if needed.

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
async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    document.getElementById("statusDot").classList.toggle("connected", data.mqtt_connected);
    document.getElementById("statusText").textContent = data.mqtt_connected
      ? "broker connected" : "broker offline";
  } catch {
    document.getElementById("statusDot").classList.remove("connected");
    document.getElementById("statusText").textContent = "app offline";
  }
}
setInterval(pollStatus, 4000);
pollStatus();

// ---------- param rendering (shared by command sheet + schedule form) ----------
function renderParamField(param) {
  const wrap = document.createElement("div");
  const label = document.createElement("label");
  label.textContent = param.name.replace(/_/g, " ");
  wrap.appendChild(label);

  let input;
  if (param.type === "color") {
    input = document.createElement("input");
    input.type = "color";
    input.value = param.default || "#ffffff";
  } else if (param.type === "slider") {
    input = document.createElement("input");
    input.type = "range";
    input.min = param.min; input.max = param.max;
    input.value = param.default ?? param.min;
    const readout = document.createElement("span");
    readout.className = "meta";
    readout.textContent = input.value;
    input.addEventListener("input", () => (readout.textContent = input.value));
    wrap.appendChild(input);
    wrap.appendChild(readout);
    input.dataset.name = param.name;
    return { el: wrap, input };
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = param.default ?? "";
  }
  input.dataset.name = param.name;
  wrap.appendChild(input);
  return { el: wrap, input };
}

function collectArgs(inputs) {
  const args = {};
  for (const input of inputs) {
    const v = input.type === "range" || input.type === "number" ? Number(input.value) : input.value;
    args[input.dataset.name] = v;
  }
  return args;
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
    card.innerHTML = `<span class="command-glyph">${cmd.label[0]}</span><span>${cmd.label}</span>`;
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
  closeRow.innerHTML = `<button aria-label="Close">&times;</button>`;
  closeRow.querySelector("button").addEventListener("click", () => backdrop.classList.remove("open"));
  sheet.appendChild(closeRow);

  const h2 = document.createElement("h2");
  h2.textContent = cmd.label;
  sheet.appendChild(h2);

  const inputs = [];
  for (const param of cmd.params) {
    const { el, input } = renderParamField(param);
    sheet.appendChild(el);
    inputs.push(input);
  }

  const sendBtn = document.createElement("button");
  sendBtn.className = "primary";
  sendBtn.textContent = "Send command";
  sendBtn.addEventListener("click", async () => {
    sendBtn.disabled = true;
    sendBtn.textContent = "Sending…";
    try {
      const res = await fetch("/api/commands/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command_id: cmd.id, args: collectArgs(inputs) }),
      });
      sendBtn.textContent = res.ok ? "Sent" : "Failed";
    } catch {
      sendBtn.textContent = "Failed";
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
  line.innerHTML = `<span class="ts">${ts}</span><span class="topic">${msg.topic}</span><span class="payload">${msg.payload}</span>`;
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
    opt.textContent = cmd.label;
    select.appendChild(opt);
  }
  form.appendChild(select);

  const paramContainer = document.createElement("div");
  form.appendChild(paramContainer);

  let currentInputs = [];
  function renderParams() {
    paramContainer.innerHTML = "";
    currentInputs = [];
    const cmd = profile.commands.find((c) => c.id === select.value);
    for (const param of cmd.params) {
      const { el, input } = renderParamField(param);
      paramContainer.appendChild(el);
      currentInputs.push(input);
    }
  }
  select.addEventListener("change", renderParams);
  renderParams();

  const dtLabel = document.createElement("label");
  dtLabel.textContent = "run at";
  form.appendChild(dtLabel);
  const dtInput = document.createElement("input");
  dtInput.type = "datetime-local";
  form.appendChild(dtInput);

  const addBtn = document.createElement("button");
  addBtn.className = "primary";
  addBtn.textContent = "Schedule command";
  addBtn.addEventListener("click", async () => {
    if (!dtInput.value) return;
    await fetch("/api/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: select.value,
        args: collectArgs(currentInputs),
        run_at: dtInput.value,
      }),
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
      <button class="btn-cancel">Cancel</button>`;
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

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

// ---------- numeric keypad ----------
// Chromium in kiosk mode doesn't pop up any on-screen keyboard for number
// inputs on this device, and every field in this app (frame/blink/rotation/
// brightness/hour/minute) is numeric anyway, so this covers the whole app
// without needing a system keyboard package. Delegated on document, so it
// applies to fields rendered later too (schedule form, time picker, etc).
const keypadBackdrop = document.createElement("div");
keypadBackdrop.className = "keypad-backdrop";
const keypad = document.createElement("div");
keypad.className = "keypad";
keypadBackdrop.appendChild(keypad);
document.body.appendChild(keypadBackdrop);

let keypadTarget = null;

function renderKeypad() {
  keypad.innerHTML = "";
  const readout = document.createElement("div");
  readout.className = "keypad-readout";
  readout.textContent = keypadTarget && keypadTarget.value !== "" ? keypadTarget.value : "0";
  keypad.appendChild(readout);

  const grid = document.createElement("div");
  grid.className = "keypad-grid";
  for (const key of ["7", "8", "9", "4", "5", "6", "1", "2", "3", "⌫", "0", "Listo"]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = key === "Listo" ? "keypad-key keypad-done" : "keypad-key";
    btn.textContent = key;
    btn.addEventListener("click", () => handleKeypadKey(key));
    grid.appendChild(btn);
  }
  keypad.appendChild(grid);
}

function handleKeypadKey(key) {
  if (!keypadTarget) return;
  if (key === "Listo") {
    closeKeypad();
    return;
  }
  if (key === "⌫") {
    keypadTarget.value = keypadTarget.value.slice(0, -1);
  } else {
    keypadTarget.value += key;
  }
  keypadTarget.dispatchEvent(new Event("input", { bubbles: true }));
  renderKeypad();
}

function closeKeypad() {
  if (keypadTarget) keypadTarget.dispatchEvent(new Event("change", { bubbles: true }));
  keypadBackdrop.classList.remove("open");
  keypadTarget = null;
}

keypadBackdrop.addEventListener("click", (e) => {
  if (e.target === keypadBackdrop) closeKeypad();
});

document.addEventListener("focusin", (e) => {
  if (!e.target.matches('input[type="number"]')) return;
  e.target.setAttribute("inputmode", "none"); // belt-and-suspenders: no OS keyboard even if one's ever configured
  keypadTarget = e.target;
  keypadTarget.value = ""; // fresh entry rather than appending after the existing/default value
  renderKeypad();
  keypadBackdrop.classList.add("open");
});

// ---------- close app ----------
// Chromium runs with --kiosk (no window chrome), so this button is the only
// way for the client to get back to the Pi's desktop.
document.getElementById("closeAppBtn").addEventListener("click", async () => {
  if (!confirm("¿Cerrar la aplicación?")) return;
  try {
    await fetch("/api/system/quit-browser", { method: "POST" });
  } catch {
    // the fetch itself may never resolve once the browser starts closing -- fine to ignore
  }
});

// ---------- update app ----------
// Runs kiosk/update_app.sh on the device (git pull, restart backend, clear
// Chromium's cache, relaunch). The backend fires it off detached and returns
// right away -- the script kills the very backend process handling this
// request partway through, so there's no meaningful response to wait for.
document.getElementById("updateAppBtn").addEventListener("click", async () => {
  if (!confirm("¿Buscar actualizaciones? Si hay novedades, se cerrará y reabrirá el navegador.")) return;
  try {
    await fetch("/api/system/update", { method: "POST" });
  } catch {
    // same as closeAppBtn -- the connection may drop before a response arrives
  }
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

  if (cmd.value_type === "hex_color_triple") {
    // SCL wants 3 back-to-back RRGGBB values (no separators, no '#'), one per strip.
    // Palette swatches instead of the native color wheel -- much easier to hit
    // accurately on a 7" touchscreen than dragging a hue/saturation picker.
    const defaults = cmd.defaults || ["#FFFFFF", "#FFFFFF", "#FFFFFF"];
    const palette = cmd.palette || defaults;
    const selected = defaults.map((d) => d.toUpperCase());

    defaults.forEach((_, i) => {
      const row = document.createElement("div");
      const rowLabel = document.createElement("label");
      rowLabel.textContent = `tira ${i + 1}`;
      row.appendChild(rowLabel);

      const swatchRow = document.createElement("div");
      swatchRow.className = "swatch-row";
      for (const color of palette) {
        const swatch = document.createElement("button");
        swatch.type = "button";
        swatch.className = "swatch";
        swatch.style.background = color;
        swatch.classList.toggle("selected", color.toUpperCase() === selected[i]);
        swatch.addEventListener("click", () => {
          selected[i] = color.toUpperCase();
          swatchRow.querySelectorAll(".swatch").forEach((el) => el.classList.remove("selected"));
          swatch.classList.add("selected");
        });
        swatchRow.appendChild(swatch);
      }
      row.appendChild(swatchRow);
      wrap.appendChild(row);
    });

    return {
      el: wrap,
      getValue: () => selected.map((c) => c.replace("#", "")).join("").toUpperCase(),
    };
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
  renderScheduleForm();
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
  view.insertBefore(line, view.firstChild); // newest on top
  view.scrollTop = 0;
}

function connectLogSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
  ws.onmessage = (evt) => appendLogLine(JSON.parse(evt.data));
  ws.onclose = () => setTimeout(connectLogSocket, 2000); // simple reconnect
}

// ---------- schedule tab: trigger a whole visual "state" at a future time ----------
// Unlike the Comandos tab (one raw command at a time), this bundles several
// commands that together define one coherent state. Every state leads with
// AUT to clear whichever mode the device was previously left in -- PPG1/SCR1
// don't clear each other or a paused frame, only AUT does (see the firmware's
// ARCHITECTURE.md Â§4.1/Â§4.1.1/Â§4.1.2) -- otherwise a scheduled "frame" state
// could still render as ping-pong if that mode was left on from an earlier
// manual command. NET/STA/AUT/CON/DIS aren't states, so they're not offered here.
function getCmd(id) {
  return profile.commands.find((c) => c.id === id);
}

function appendField(container, cmd) {
  const field = renderValueField(cmd);
  // renderValueField's own label just says "valor"/"valor (ms)" -- fine when a
  // single command sheet already shows the command name as its title, but here
  // several fields stack together (e.g. BLK and ROT both being "valor (ms)"
  // made them indistinguishable), so relabel with the command's own name instead.
  const label = field.el.querySelector("label");
  if (label) label.textContent = cmd.unit ? `${cmd.label} (${cmd.unit})` : cmd.label;
  container.appendChild(field.el);
  return field;
}

const SCHEDULE_STATES = {
  frame: {
    label: "Escena",
    fields(container) {
      const frm = appendField(container, getCmd("FRM"));

      const invRow = document.createElement("div");
      invRow.className = "checkbox-row";
      const invLabel = document.createElement("label");
      invLabel.textContent = "Invertir dirección de rotación";
      const invCheckbox = document.createElement("input");
      invCheckbox.type = "checkbox";
      invRow.appendChild(invLabel);
      invRow.appendChild(invCheckbox);
      container.appendChild(invRow);

      const blk = appendField(container, getCmd("BLK"));
      const rot = appendField(container, getCmd("ROT"));

      return () => {
        const frame = frm.getValue();
        const commands = [{ command_id: "AUT" }, { command_id: "FRM", value: frame }];
        if (invCheckbox.checked) commands.push({ command_id: "INV" });
        commands.push({ command_id: "BLK", value: blk.getValue() });
        commands.push({ command_id: "ROT", value: rot.getValue() });
        return { commands, label: `Escena ${frame}${invCheckbox.checked ? " (invertido)" : ""}` };
      };
    },
  },
  pingpong: {
    label: "Ping-pong",
    fields(container) {
      const ppc = appendField(container, getCmd("PPC"));
      const ppk = appendField(container, getCmd("PPK"));
      const rot = appendField(container, getCmd("ROT"));
      const bri = appendField(container, getCmd("BRI"));
      const blk = appendField(container, getCmd("BLK"));

      return () => ({
        commands: [
          { command_id: "AUT" },
          { command_id: "PPG", value: 1 },
          { command_id: "PPC", value: ppc.getValue() },
          { command_id: "PPK", value: ppk.getValue() },
          { command_id: "ROT", value: rot.getValue() },
          { command_id: "BRI", value: bri.getValue() },
          { command_id: "BLK", value: blk.getValue() },
        ],
        label: "Ping-pong",
      });
    },
  },
  stripcolor: {
    label: "Rotación de color por tira",
    fields(container) {
      const scl = appendField(container, getCmd("SCL"));
      const rot = appendField(container, getCmd("ROT"));
      const bri = appendField(container, getCmd("BRI"));
      const blk = appendField(container, getCmd("BLK"));

      return () => ({
        commands: [
          { command_id: "AUT" },
          { command_id: "SCR", value: 1 },
          { command_id: "SCL", value: scl.getValue() },
          { command_id: "ROT", value: rot.getValue() },
          { command_id: "BRI", value: bri.getValue() },
          { command_id: "BLK", value: blk.getValue() },
        ],
        label: "Rotación de color por tira",
      });
    },
  },
};

// ---------- custom date/time picker ----------
// A native <input type="datetime-local">'s pop-up calendar is rendered by the
// browser itself (not page content), so it can't be resized via CSS -- it stays
// small regardless of how big we make the input field. This builds an
// equivalent widget entirely out of our own touch-sized elements instead.
const MESES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];
const DIAS_SEMANA = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sa", "Do"];

function pad2(n) {
  return String(n).padStart(2, "0");
}

function createDateTimePicker() {
  const now = new Date();
  // Default to "an hour from now" (via a real Date add, so a near-midnight
  // default correctly rolls over into tomorrow) and pre-select that day --
  // otherwise the "Programar estado" button had nothing to submit until the
  // user explicitly tapped a day, and silently did nothing when they didn't.
  const defaultDt = new Date(now.getTime() + 60 * 60 * 1000);
  let viewYear = defaultDt.getFullYear();
  let viewMonth = defaultDt.getMonth();
  let selectedDay = { year: defaultDt.getFullYear(), month: defaultDt.getMonth(), day: defaultDt.getDate() };
  let hour = defaultDt.getHours();
  let minute = 0;

  const el = document.createElement("div");
  el.className = "datetime-picker";

  const header = document.createElement("div");
  header.className = "cal-header";
  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "cal-nav";
  prevBtn.textContent = "‹";
  const title = document.createElement("span");
  title.className = "cal-title";
  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "cal-nav";
  nextBtn.textContent = "›";
  header.appendChild(prevBtn);
  header.appendChild(title);
  header.appendChild(nextBtn);
  el.appendChild(header);

  const weekdays = document.createElement("div");
  weekdays.className = "cal-weekdays";
  for (const d of DIAS_SEMANA) {
    const s = document.createElement("span");
    s.textContent = d;
    weekdays.appendChild(s);
  }
  el.appendChild(weekdays);

  const grid = document.createElement("div");
  grid.className = "cal-grid";
  el.appendChild(grid);

  const timeRow = document.createElement("div");
  timeRow.className = "time-row";
  const timeLabel = document.createElement("label");
  timeLabel.textContent = "hora";
  const timeInputs = document.createElement("div");
  timeInputs.className = "time-inputs";
  const hhInput = document.createElement("input");
  hhInput.type = "number";
  hhInput.min = 0;
  hhInput.max = 23;
  const sep = document.createElement("span");
  sep.textContent = ":";
  const mmInput = document.createElement("input");
  mmInput.type = "number";
  mmInput.min = 0;
  mmInput.max = 59;
  timeInputs.appendChild(hhInput);
  timeInputs.appendChild(sep);
  timeInputs.appendChild(mmInput);
  timeRow.appendChild(timeLabel);
  timeRow.appendChild(timeInputs);
  el.appendChild(timeRow);

  function isBeforeCurrentMonth(y, m) {
    return y < now.getFullYear() || (y === now.getFullYear() && m < now.getMonth());
  }

  function renderGrid() {
    grid.innerHTML = "";
    const offset = (new Date(viewYear, viewMonth, 1).getDay() + 6) % 7; // Monday-first
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const todayY = now.getFullYear(), todayM = now.getMonth(), todayD = now.getDate();

    for (let i = 0; i < offset; i++) {
      const empty = document.createElement("span");
      empty.className = "cal-day empty";
      grid.appendChild(empty);
    }

    for (let day = 1; day <= daysInMonth; day++) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cal-day";
      btn.textContent = day;

      const isPast = viewYear < todayY
        || (viewYear === todayY && viewMonth < todayM)
        || (viewYear === todayY && viewMonth === todayM && day < todayD);
      if (isPast) btn.disabled = true;

      if (viewYear === todayY && viewMonth === todayM && day === todayD) {
        btn.classList.add("today");
      }
      if (selectedDay && selectedDay.year === viewYear && selectedDay.month === viewMonth && selectedDay.day === day) {
        btn.classList.add("selected");
      }

      btn.addEventListener("click", () => {
        selectedDay = { year: viewYear, month: viewMonth, day };
        renderGrid();
      });
      grid.appendChild(btn);
    }
  }

  function renderCalendar() {
    title.textContent = `${MESES[viewMonth][0].toUpperCase()}${MESES[viewMonth].slice(1)} ${viewYear}`;
    const prev = viewMonth === 0 ? { y: viewYear - 1, m: 11 } : { y: viewYear, m: viewMonth - 1 };
    prevBtn.disabled = isBeforeCurrentMonth(prev.y, prev.m);
    renderGrid();
  }

  prevBtn.addEventListener("click", () => {
    if (prevBtn.disabled) return;
    if (viewMonth === 0) { viewMonth = 11; viewYear -= 1; } else { viewMonth -= 1; }
    renderCalendar();
  });
  nextBtn.addEventListener("click", () => {
    if (viewMonth === 11) { viewMonth = 0; viewYear += 1; } else { viewMonth += 1; }
    renderCalendar();
  });

  hhInput.value = pad2(hour);
  mmInput.value = pad2(minute);
  hhInput.addEventListener("change", () => {
    hour = Math.max(0, Math.min(23, Number(hhInput.value) || 0));
    hhInput.value = pad2(hour);
  });
  mmInput.addEventListener("change", () => {
    minute = Math.max(0, Math.min(59, Number(mmInput.value) || 0));
    mmInput.value = pad2(minute);
  });

  renderCalendar();

  return {
    el,
    getValue() {
      if (!selectedDay) return null;
      return `${selectedDay.year}-${pad2(selectedDay.month + 1)}-${pad2(selectedDay.day)}T${pad2(hour)}:${pad2(minute)}`;
    },
  };
}

function renderScheduleForm() {
  const form = document.getElementById("scheduleForm");
  form.innerHTML = "";

  const typeLabel = document.createElement("label");
  typeLabel.textContent = "tipo de estado";
  form.appendChild(typeLabel);

  const typeSelect = document.createElement("select");
  for (const [key, def] of Object.entries(SCHEDULE_STATES)) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = def.label;
    typeSelect.appendChild(opt);
  }
  form.appendChild(typeSelect);

  const fieldContainer = document.createElement("div");
  form.appendChild(fieldContainer);

  let collect = null;
  function renderFields() {
    fieldContainer.innerHTML = "";
    collect = SCHEDULE_STATES[typeSelect.value].fields(fieldContainer);
  }
  typeSelect.addEventListener("change", renderFields);
  renderFields();

  const dtLabel = document.createElement("label");
  dtLabel.textContent = "ejecutar el";
  form.appendChild(dtLabel);
  const picker = createDateTimePicker();
  form.appendChild(picker.el);

  const addBtn = document.createElement("button");
  addBtn.className = "primary";
  addBtn.textContent = "Programar estado";
  addBtn.addEventListener("click", async () => {
    const runAt = picker.getValue();
    if (!runAt) return;
    const { commands, label } = collect();
    addBtn.disabled = true;
    try {
      const res = await fetch("/api/schedule", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, commands, run_at: runAt }),
      });
      addBtn.textContent = res.ok ? "Programado" : "Error";
    } catch {
      addBtn.textContent = "Error";
    }
    loadSchedules();
    setTimeout(() => {
      addBtn.disabled = false;
      addBtn.textContent = "Programar estado";
    }, 1200);
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
        <div>${row.label}</div>
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
connectLogSocket();
loadSchedules();
setInterval(loadSchedules, 15000);

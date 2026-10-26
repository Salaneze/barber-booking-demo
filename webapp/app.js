"use strict";
// Mini App записи: услуга -> мастер -> день и время на одном экране -> контакты.
// Сервер ничему отсюда не верит: кто записывается, он берёт из подписи initData,
// свободно ли время — пересчитывает сам. Здесь только показ и выбор.

const tg = window.Telegram && window.Telegram.WebApp;
const INIT = tg ? tg.initData : "";
const inTg = Boolean(INIT);
const view = document.getElementById("view");

const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

const S = {
  tab: "book",
  info: null,            // /api/init: услуги, мастера, окно, сохранённый клиент
  step: "service",       // service | master | when | confirm | done
  service: null, master: 0, replace: 0,
  days: null,            // {"2026-10-30": 5}
  month: null,           // показанный месяц, "YYYY-MM"
  day: null, slots: null, slot: null,
  booking: null,
};

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// toLocaleString ставит неразрывные пробелы разных видов — \s ловит их все
const rub = (n) => n.toLocaleString("ru-RU").replace(/\s/g, " ") + " ₽";
const plural = (n, a, b, c) => { const m = n % 100, k = n % 10; return m > 10 && m < 20 ? c : k === 1 ? a : k >= 2 && k <= 4 ? b : c; };
const parseDay = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const dayWords = (iso) => { const d = parseDay(iso); return `${WD[(d.getUTCDay() + 6) % 7]}, ${d.getUTCDate()} ${MONTHS_GEN[d.getUTCMonth()]}`; };
const haptic = (kind) => { try { kind === "ok" ? tg.HapticFeedback.notificationOccurred("success") : tg.HapticFeedback.selectionChanged(); } catch (_) { /* вне Telegram вибро нет */ } };

async function api(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { Authorization: "tma " + INIT, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Сервер не ответил. Попробуйте ещё раз.");
  return data;
}

function alertMsg(text) { inTg && tg.showAlert ? tg.showAlert(text) : window.alert(text); }

// Нижняя кнопка: в Telegram — родная MainButton, вне его — своя полоса
const bar = document.getElementById("bar"), barBtn = document.getElementById("bar-btn");
let barAction = null;
function mainButton(text, action) {
  barAction = action;
  if (inTg) {
    if (!text) return tg.MainButton.hide();
    tg.MainButton.setParams({ text, color: "#c23b22", text_color: "#ffffff", is_active: true, is_visible: true });
  } else {
    bar.hidden = !text;
    barBtn.textContent = text || "";
  }
}
function busy(on) {
  if (inTg) on ? tg.MainButton.showProgress(false) : tg.MainButton.hideProgress();
  barBtn.disabled = on;
}
const runBar = () => barAction && barAction();
inTg ? tg.MainButton.onClick(runBar) : barBtn.addEventListener("click", runBar);

// «Назад» — родная кнопка в шапке Telegram
const BACK = { master: "service", when: "master", confirm: "when" };
function syncBack() {
  if (!inTg) return;
  S.tab === "book" && BACK[S.step] ? tg.BackButton.show() : tg.BackButton.hide();
}
function goBack() {
  const to = BACK[S.step];
  if (to) go(to);
}
if (tg) tg.BackButton.onClick(goBack);

function go(step) { S.step = step; render(); }
function masters() { return S.info.masters.filter((m) => m.services.includes(S.service.id)); }

function render() {
  syncBack();
  mainButton(null);
  if (S.tab === "my") return renderMy();
  ({ service: renderServices, master: renderMasters, when: renderWhen, confirm: renderConfirm, done: renderDone })[S.step]();
  window.scrollTo(0, 0);
}

function renderServices() {
  S.replace = 0;
  view.innerHTML = `<p class="step">Шаг 1 из 4</p><h1>Выберите услугу</h1><div class="list">${
    S.info.services.map((s) => `<button class="row" data-s="${s.id}"><b>${esc(s.title)}</b><span class="price">${rub(s.price)}</span><span class="muted">${s.minutes} мин</span></button>`).join("")
  }</div>`;
  view.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => {
    haptic(); S.service = S.info.services.find((s) => s.id === +b.dataset.s); go("master");
  }));
}

function renderMasters() {
  const list = masters();
  const any = list.length > 1 ? `<button class="row" data-m="0"><b>Любой свободный мастер</b><span class="muted">Покажем всё свободное время</span></button>` : "";
  view.innerHTML = `<p class="step">Шаг 2 из 4 · ${esc(S.service.title)}</p><h1>${S.replace ? "Перенос: к кому" : "К кому записать?"}</h1><div class="list">${any}${
    list.map((m) => `<button class="row" data-m="${m.id}"><b>${esc(m.name)}</b><span class="muted">${esc(m.about)}</span></button>`).join("")
  }</div>`;
  view.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => {
    haptic(); S.master = +b.dataset.m; S.days = null; S.day = null; S.slot = null; S.month = null; go("when");
  }));
}

async function renderWhen() {
  const who = S.master ? S.info.masters.find((m) => m.id === S.master).name : "любой мастер";
  view.innerHTML = `<p class="step">Шаг 3 из 4 · ${esc(S.service.title)} · ${esc(who)}</p><h1>Когда удобно?</h1><div id="cal"><div class="skel tall"></div></div><div id="times"></div>`;
  if (!S.days) {
    try {
      const q = new URLSearchParams({ service: S.service.id, master: S.master, replace: S.replace });
      const r = await api("/api/calendar?" + q);
      S.days = Object.fromEntries(r.days.map((x) => [x.d, x.free]));
      // Открываем месяц первого свободного дня: окно на стыке месяцев иначе начинается с пустоты
      const first = r.days.find((x) => x.free) || r.days[0];
      S.month = first.d.slice(0, 7);
      if (!r.days.some((x) => x.free)) {
        document.getElementById("cal").innerHTML = `<p class="empty">На ${S.info.days_ahead} ${plural(S.info.days_ahead, "день", "дня", "дней")} вперёд свободного времени нет.<br>Попробуйте другого мастера.</p>`;
        return;
      }
    } catch (e) { return alertMsg(e.message); }
  }
  drawCalendar();
  if (S.day) drawTimes();
}

function drawCalendar() {
  const [y, m] = S.month.split("-").map(Number);
  const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7;
  const total = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const months = [...new Set(Object.keys(S.days).map((k) => k.slice(0, 7)))];
  const mi = months.indexOf(S.month);
  let cells = WD.map((d) => `<span class="wd">${d}</span>`).join("") + "<span></span>".repeat(lead);
  for (let d = 1; d <= total; d++) {
    const iso = `${S.month}-${String(d).padStart(2, "0")}`;
    const n = S.days[iso];
    let cls = "day", attrs = "disabled", label = `${d} ${MONTHS_GEN[m - 1]}`;
    if (n === undefined) cls += " out";
    else if (n === 0) { cls += " full"; label += ", записи нет"; }
    else { cls += " free"; attrs = `data-d="${iso}"`; label += `, свободно ${n}`; }
    if (iso === S.info.today) cls += " today";
    if (iso === S.day) cls += " is-on";
    cells += `<button class="${cls}" ${attrs} aria-label="${label}">${d}${n ? `<small>${n}</small>` : ""}</button>`;
  }
  document.getElementById("cal").innerHTML = `
    <div class="cal-head">
      <button class="nav" data-mv="-1" ${mi <= 0 ? "disabled" : ""} aria-label="Предыдущий месяц">‹</button>
      <b>${MONTHS[m - 1]} ${y}</b>
      <button class="nav" data-mv="1" ${mi >= months.length - 1 ? "disabled" : ""} aria-label="Следующий месяц">›</button>
    </div>
    <div class="cal">${cells}</div>
    <p class="legend">Цифра под датой — сколько свободных окон</p>`;
  document.querySelectorAll("[data-mv]").forEach((b) => b.addEventListener("click", () => {
    S.month = months[mi + +b.dataset.mv]; drawCalendar();
  }));
  document.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => {
    haptic(); S.day = b.dataset.d; S.slot = null; drawCalendar(); drawTimes();
  }));
}

async function drawTimes() {
  const box = document.getElementById("times");
  const head = `<p class="times-head">${dayWords(S.day)}</p>`;
  mainButton(null);
  box.innerHTML = head + `<div class="skel"></div>`;
  try {
    const q = new URLSearchParams({ service: S.service.id, master: S.master, replace: S.replace, day: S.day });
    S.slots = (await api("/api/slots?" + q)).slots;
  } catch (e) { return alertMsg(e.message); }
  if (!S.slots.length) {
    box.innerHTML = head + `<p class="muted">Время на этот день уже разобрали — выберите другой.</p>`;
    return;
  }
  box.innerHTML = head + `<div class="times">${
    S.slots.map((s, i) => `<button class="time${S.slot && S.slot.at === s.at ? " is-on" : ""}" data-i="${i}">${s.t}</button>`).join("")
  }</div>`;
  box.querySelectorAll("[data-i]").forEach((b) => b.addEventListener("click", () => {
    haptic(); S.slot = S.slots[+b.dataset.i];
    box.querySelectorAll(".time").forEach((x) => x.classList.toggle("is-on", x === b));
    mainButton(`Дальше · ${dayWords(S.day).split(", ")[1]}, ${S.slot.t}`, () => go("confirm"));
  }));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderConfirm() {
  const who = S.master ? S.info.masters.find((m) => m.id === S.master).name : "любой свободный";
  const tgName = tg && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.first_name || "" : "";
  const c = S.info.client || { name: tgName, phone: "" };
  view.innerHTML = `<p class="step">Шаг 4 из 4</p><h1>Проверьте запись</h1>
    <div class="card"><dl>
      <dt>Услуга</dt><dd>${esc(S.service.title)} · ${S.service.minutes} мин</dd>
      <dt>Когда</dt><dd>${dayWords(S.day)}, ${S.slot.t}</dd>
      <dt>Мастер</dt><dd>${esc(who)}</dd>
      <dt>Цена</dt><dd>${rub(S.service.price)}</dd>
    </dl></div>
    <label for="nm">Как к вам обращаться</label><input id="nm" autocomplete="name" maxlength="64" value="${esc(c.name)}">
    <label for="ph">Телефон для связи</label><input id="ph" type="tel" autocomplete="tel" inputmode="tel" placeholder="+7 900 123-45-67" value="${esc(c.phone)}">
    <p class="muted">Отменить или перенести можно не позже чем за ${S.info.cancel_hours} ${plural(S.info.cancel_hours, "час", "часа", "часов")} до визита.</p>`;
  mainButton(S.replace ? "Перенести запись" : "Записаться", submit);
}

async function submit() {
  const name = document.getElementById("nm").value.trim();
  const phone = document.getElementById("ph").value.trim();
  if (!name) return alertMsg("Напишите имя.");
  if (phone.replace(/\D/g, "").length < 10) return alertMsg("Напишите телефон, например +7 900 123-45-67.");
  busy(true);
  try {
    const r = await api("/api/book", { service: S.service.id, master: S.master, at: S.slot.at, name, phone, replace: S.replace });
    S.booking = r.booking;
    S.info.client = { name, phone };
    haptic("ok");
    go("done");
  } catch (e) {
    alertMsg(e.message);
    // Время заняли, пока человек вводил телефон, — возвращаем к свежему расписанию
    if (/заняли/.test(e.message)) { S.days = null; S.slot = null; S.day = null; go("when"); }
  } finally { busy(false); }
}

function renderDone() {
  const b = S.booking;
  view.innerHTML = `<div class="done"><div class="mark" aria-hidden="true">✓</div>
    <h1>${S.replace ? "Запись перенесена" : "Вы записаны"}</h1>
    <p><b>${esc(b.service)}</b> · ${rub(b.price)}<br>${esc(b.when)}<br>Мастер: ${esc(b.master)}</p>
    <p class="muted">${esc(S.info.address)}<br>Подтверждение пришло в чат, там же будут напоминания.</p></div>`;
  S.replace = 0;
  mainButton("Готово", () => (inTg ? tg.close() : switchTab("my")));
}

async function renderMy() {
  view.innerHTML = `<div class="list"><div class="skel"></div><div class="skel"></div></div>`;
  let items;
  try { items = (await api("/api/my")).bookings; } catch (e) { return alertMsg(e.message); }
  if (!items.length) {
    view.innerHTML = `<p class="empty">Активных записей нет.</p>`;
    mainButton("Записаться", () => switchTab("book"));
    return;
  }
  view.innerHTML = `<div class="list">${items.map((b) => `<div class="bk">
      <b>${esc(b.service)} · ${rub(b.price)}</b><span>${esc(b.when)}</span><br><span class="muted">Мастер: ${esc(b.master)}</span>
      <div class="acts"><button class="ghost" data-mv="${b.id}">Перенести</button><button class="ghost danger" data-x="${b.id}">Отменить</button></div>
    </div>`).join("")}</div><p class="muted note">Отменить или перенести можно не позже чем за ${S.info.cancel_hours} ${plural(S.info.cancel_hours, "час", "часа", "часов")} до визита.</p>`;
  view.querySelectorAll("[data-x]").forEach((btn) => btn.addEventListener("click", () => cancel(+btn.dataset.x)));
  view.querySelectorAll("[data-mv]").forEach((btn) => btn.addEventListener("click", () => {
    const b = items.find((x) => x.id === +btn.dataset.mv);
    S.tab = "book"; markTabs();
    S.replace = b.id; S.service = S.info.services.find((s) => s.id === b.service_id);
    S.master = b.master_id; S.days = null; S.day = null; S.slot = null;
    go("when");
  }));
}

function cancel(id) {
  const doIt = async (yes) => {
    if (!yes) return;
    try { await api("/api/cancel", { id }); haptic("ok"); renderMy(); } catch (e) { alertMsg(e.message); }
  };
  inTg && tg.showConfirm ? tg.showConfirm("Отменить запись?", doIt) : doIt(window.confirm("Отменить запись?"));
}

function markTabs() {
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.tab === S.tab;
    t.classList.toggle("is-on", on);
    t.setAttribute("aria-selected", on);
  });
}
function switchTab(tab) {
  S.tab = tab; markTabs();
  if (tab === "book" && S.step === "done") S.step = "service";
  render();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

// Схему берём у клиента Telegram, цвета — свои. Шапку и низ окна красим
// сами, иначе над бумажным фоном висит полоса из темы пользователя
function applyTheme() {
  const dark = tg && tg.colorScheme ? tg.colorScheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (!tg) return;
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
  try {
    if (tg.isVersionAtLeast("6.1")) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); }
    if (tg.isVersionAtLeast("7.10")) tg.setBottomBarColor(bg);
  } catch (_) { /* старый клиент: останутся цвета его темы, вёрстка не страдает */ }
}

async function boot() {
  applyTheme();
  if (tg) { tg.onEvent("themeChanged", applyTheme); tg.ready(); tg.expand(); }
  if (!inTg) {
    document.querySelector(".top").hidden = true;
    view.innerHTML = `<div class="empty"><h1>Запись открывается в Telegram</h1><p>Откройте бота и нажмите «Записаться» слева от поля ввода.</p></div>`;
    return;
  }
  view.innerHTML = `<div class="list"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>`;
  try { S.info = await api("/api/init"); } catch (e) {
    view.innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    return;
  }
  render();
}
boot();

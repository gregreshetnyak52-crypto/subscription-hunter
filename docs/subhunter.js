/*
 * Subscription Hunter — браузерная версия ядра.
 * Повторяет логику skills/subscription-hunter/scripts/subhunter.py шаг в шаг;
 * тесты (tests/test_web.py) проверяют, что обе версии дают одинаковый результат.
 * Работает целиком в браузере: выписка никуда не отправляется.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.SubHunter = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PERIODS = [
    ["неделя", 7, 1],
    ["месяц", 30.44, 4],
    ["квартал", 91.3, 7],
    ["полгода", 182.6, 10],
    ["год", 365.25, 12],
  ];

  const HEADER_KEYWORDS = {
    date: ["дата операции", "дата транзакции", "дата", "date"],
    amount: ["сумма операции", "сумма в валюте счета", "сумма в валюте счёта", "сумма", "amount"],
    debit: ["расход", "списание", "debit", "дебет"],
    credit: ["приход", "зачисление", "поступление", "credit", "кредит"],
    description: ["описание", "назначение платежа", "назначение", "наименование", "merchant",
      "контрагент", "получатель", "description", "details", "место"],
    category: ["категория", "category", "mcc"],
    status: ["статус", "status"],
  };
  const COLUMN_ORDER = ["date", "amount", "debit", "credit", "description", "category", "status"];
  const FAILED_STATUSES = ["отклон", "failed", "declined", "отмен", "cancel"];

  // \b в JavaScript не понимает кириллицу, поэтому граница слова задаётся явно.
  const W = "[\\p{L}\\p{N}_]";
  const BOUNDARY = `(?:(?<=${W})(?!${W})|(?<!${W})(?=${W}))`;
  function pyRegex(source, flags) {
    return new RegExp(source.replace(/\\b/g, BOUNDARY), (flags || "") + "u");
  }

  const NOISE_WORDS = pyRegex(
    "\\b(оплата|покупка|списание|платеж|платёж|регулярный|подписка|подписки|оплата услуг|" +
    "retail|purchase|payment|card|карта|карты|rus|ru|msk|moscow|москва|moskva|g|г|spb|" +
    "www|com|sbp|сбп)\\b", "g");

  // Python round(): банковское округление (2.5 → 2).
  function pyRound(x) {
    const floor = Math.floor(x);
    const diff = x - floor;
    if (Math.abs(diff - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1;
    return Math.round(x);
  }

  function median(values) {
    const s = [...values].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  // ---------- даты (только календарные дни, без часовых поясов) ----------

  function makeDay(y, m, d) {
    const t = Date.UTC(y, m - 1, d);
    const check = new Date(t);
    if (check.getUTCFullYear() !== y || check.getUTCMonth() !== m - 1 || check.getUTCDate() !== d) return null;
    return Math.round(t / 86400000);
  }
  function dayToDate(day) { return new Date(day * 86400000); }
  function pad(n) { return String(n).padStart(2, "0"); }
  function formatDay(day) {
    const d = dayToDate(day);
    return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
  }
  function isoDay(day) {
    const d = dayToDate(day);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  }

  function parseDate(value) {
    const text = String(value || "").trim().split(/\s+/)[0] || "";
    let m;
    if ((m = text.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/))) return makeDay(+m[3], +m[2], +m[1]);
    if ((m = text.match(/^(\d{1,2})\.(\d{1,2})\.(\d{2})$/))) {
      const yy = +m[3];
      return makeDay(yy < 69 ? 2000 + yy : 1900 + yy, +m[2], +m[1]);
    }
    if ((m = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) return makeDay(+m[1], +m[2], +m[3]);
    if ((m = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/))) return makeDay(+m[3], +m[2], +m[1]);
    if ((m = text.match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/))) return makeDay(+m[3], +m[2], +m[1]);
    return null;
  }

  // ---------- чтение выписки ----------

  function decode(buffer) {
    const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes).replace(/^﻿/, "");
    } catch (e) {
      return new TextDecoder("windows-1251").decode(bytes);
    }
  }

  function sniffDelimiter(text) {
    const sample = text.split(/\r\n|\r|\n/).slice(0, 30).join("\n");
    let best = ";", count = -1;
    for (const d of [";", "\t", ","]) {
      const c = sample.split(d).length - 1;
      if (c > count) { best = d; count = c; }
    }
    return best;
  }

  function parseCsv(text, delimiter) {
    const rows = [];
    let row = [], field = "", quoted = false, i = 0;
    while (i < text.length) {
      const ch = text[i];
      if (quoted) {
        if (ch === '"') {
          if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
          quoted = false; i++; continue;
        }
        field += ch; i++; continue;
      }
      if (ch === '"' && field === "") { quoted = true; i++; continue; }
      if (ch === delimiter) { row.push(field); field = ""; i++; continue; }
      if (ch === "\r" || ch === "\n") {
        row.push(field); rows.push(row); row = []; field = "";
        if (ch === "\r" && text[i + 1] === "\n") i++;
        i++; continue;
      }
      field += ch; i++;
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    return rows;
  }

  function findColumn(header, kind, taken) {
    const names = header.map((h) => String(h).trim().toLowerCase());
    for (const keyword of HEADER_KEYWORDS[kind]) {
      for (let i = 0; i < names.length; i++) {
        if (!taken.has(i) && (names[i] === keyword || names[i].startsWith(keyword))) return i;
      }
    }
    for (const keyword of HEADER_KEYWORDS[kind]) {
      for (let i = 0; i < names.length; i++) {
        if (!taken.has(i) && names[i].includes(keyword)) return i;
      }
    }
    return null;
  }

  function mapColumns(header) {
    const columns = {}, taken = new Set();
    for (const kind of COLUMN_ORDER) {
      const index = findColumn(header, kind, taken);
      if (index !== null) { columns[kind] = index; taken.add(index); }
    }
    return columns;
  }

  function parseAmount(value) {
    if (value === null || value === undefined) return null;
    let text = String(value).trim().replace(/ /g, "").replace(/ /g, "").replace(/−/g, "-");
    text = text.replace(/[^\d,.\-+()]/g, "");
    if (!text || !/\d/.test(text)) return null;
    const negative = text.startsWith("-") || (text.startsWith("(") && text.endsWith(")"));
    text = text.replace(/^[()+-]+|[()+-]+$/g, "");
    if (text.includes(",") && text.includes(".")) {
      text = text.lastIndexOf(".") > text.lastIndexOf(",")
        ? text.replace(/,/g, "") : text.replace(/\./g, "").replace(/,/g, ".");
    } else {
      text = text.replace(/,/g, ".");
    }
    if (!/^\d*\.?\d+$|^\d+\.?\d*$/.test(text)) return null;
    const number = parseFloat(text);
    if (Number.isNaN(number)) return null;
    return negative ? -number : number;
  }

  function readStatement(text, name) {
    const rows = parseCsv(text, sniffDelimiter(text));
    let headerIndex = null, columns = null;
    for (let i = 0; i < Math.min(rows.length, 40); i++) {
      const candidate = mapColumns(rows[i]);
      if ("date" in candidate && "description" in candidate && ("amount" in candidate || "debit" in candidate)) {
        headerIndex = i; columns = candidate; break;
      }
    }
    if (headerIndex === null) {
      throw new Error(`${name}: не нашёл строку заголовков с датой, суммой и описанием операции`);
    }
    const warnings = [], parsed = [];
    for (const row of rows.slice(headerIndex + 1)) {
      if (!row.length || row.every((c) => !c.trim())) continue;
      const cell = (kind) => (kind in columns && columns[kind] < row.length ? row[columns[kind]] : "");
      const status = cell("status").toLowerCase();
      if (FAILED_STATUSES.some((s) => status.includes(s))) continue;
      const day = parseDate(cell("date"));
      if (day === null) continue;
      let amount;
      if ("debit" in columns) {
        const debit = parseAmount(cell("debit"));
        amount = debit ? -Math.abs(debit) : null;
        if (amount === null && "amount" in columns) amount = parseAmount(cell("amount"));
      } else {
        amount = parseAmount(cell("amount"));
      }
      if (amount === null) continue;
      parsed.push({ day, amount, description: cell("description").trim(), category: cell("category").trim() });
    }
    if (!parsed.length) throw new Error(`${name}: в выписке не нашлось операций`);
    const hasNegative = parsed.some((p) => p.amount < 0);
    if (!hasNegative) warnings.push(`${name}: в выписке нет отрицательных сумм — считаю все операции списаниями`);
    const transactions = parsed
      .filter((p) => (p.amount < 0 || !hasNegative) && p.amount !== 0)
      .map((p) => ({ day: p.day, amount: Math.abs(p.amount), description: p.description, category: p.category }));
    return { transactions, warnings };
  }

  // ---------- поиск подписок ----------

  function prepareServices(data) {
    return data.services.map((s) => ({ ...s, _re: s.patterns.map((p) => pyRegex(p)) }));
  }

  function matchService(description, services) {
    const text = description.toLowerCase().replace(/ё/g, "е");
    for (const service of services) {
      if (service._re.some((re) => re.test(text))) return service;
    }
    return null;
  }

  function merchantKey(description) {
    let text = description.toLowerCase().replace(/ё/g, "е");
    text = text.replace(/\d{3,}/g, " ");
    text = text.replace(/[^a-zа-я0-9\s]/gu, " ");
    text = text.replace(NOISE_WORDS, " ");
    const words = text.split(/\s+/).filter((w) => w.length > 1);
    return words.slice(0, 3).join(" ") || description.trim().toLowerCase();
  }

  function prettyName(charges) {
    const description = charges[charges.length - 1].description.trim();
    return description.replace(/\s+/g, " ").slice(0, 60) || "Без описания";
  }

  function fitsPeriod(intervals, days, tolerance) {
    let good = 0;
    for (const interval of intervals) {
      const k = Math.max(1, pyRound(interval / days));
      if (Math.abs(interval - k * days) <= tolerance * k && k <= 3) good++;
    }
    return good / intervals.length;
  }

  function detectPeriod(intervals) {
    let best = null;
    for (const [name, days, tolerance] of PERIODS) {
      const share = fitsPeriod(intervals, days, tolerance);
      // При равной доле выбираем более длинный период: 366 дней — это год, а не два полугодия.
      if (share >= 0.75 && (best === null || share >= best.share)) best = { name, days, share };
    }
    return best;
  }

  function amountsConsistent(charges) {
    const m = median(charges.map((c) => c.amount));
    const close = charges.filter((c) => Math.abs(c.amount - m) <= 0.3 * m).length;
    return close / charges.length >= 0.6;
  }

  function makeSubscription(fields) {
    const sub = { notes: [], active: true, confidence: "высокая", service: null, ...fields };
    sub.last = sub.charges[sub.charges.length - 1];
    sub.monthly = (sub.last.amount * 30.44) / sub.periodDays;
    sub.yearly = sub.monthly * 12;
    sub.nextExpected = sub.last.day + pyRound(sub.periodDays);
    return sub;
  }

  function findSubscriptions(transactions, services, today) {
    if (today === undefined || today === null) today = Math.max(...transactions.map((t) => t.day));
    const sorted = transactions.map((t, i) => ({ t, i }))
      .sort((a, b) => a.t.day - b.t.day || a.i - b.i).map((x) => x.t);
    const groups = new Map();
    for (const t of sorted) {
      const service = matchService(t.description, services);
      const key = service ? `service:${service.id}` : merchantKey(t.description);
      if (!groups.has(key)) groups.set(key, { service, charges: [] });
      groups.get(key).charges.push(t);
    }
    const found = [], maybe = [];
    for (const [key, group] of groups) {
      const { charges, service } = group;
      const days = [...new Set(charges.map((c) => c.day))].sort((a, b) => a - b);
      if (days.length >= 2) {
        const intervals = days.slice(1).map((d, i) => d - days[i]);
        const period = detectPeriod(intervals);
        if (period && (amountsConsistent(charges) || service)) {
          const perDay = new Map();
          for (const c of charges) perDay.set(c.day, c);
          const ordered = days.map((d) => perDay.get(d));
          const sub = makeSubscription({
            name: service ? service.name : prettyName(ordered), key, period: period.name,
            periodDays: period.days, charges: ordered, service,
            confidence: ordered.length >= 3 ? "высокая" : "средняя",
          });
          annotate(sub, today);
          found.push(sub);
          continue;
        }
      }
      if (service) {
        const sub = makeSubscription({
          name: service.name, key, period: "неизвестно", periodDays: 30.44, charges, service, confidence: "низкая",
        });
        sub.notes.push("одно списание известного сервиса — проверьте, подписка ли это");
        maybe.push(sub);
      }
    }
    found.sort((a, b) => b.yearly - a.yearly);
    return { found, maybe, today };
  }

  function annotate(sub, today) {
    const amounts = sub.charges.map((c) => c.amount);
    let first = amounts[0];
    const last = amounts[amounts.length - 1];
    const later = median(amounts.slice(1));
    if (amounts.length >= 2 && first <= 0.1 * later) {
      sub.notes.push(`началась с пробного периода за ${money(first)}`);
      first = amounts[1];
    }
    if (first && last > first * 1.05) {
      sub.notes.push(`цена выросла на ${pyRound((last / first - 1) * 100)}%: было ${money(first)}, стало ${money(last)}`);
    }
    const grace = Math.max(5, sub.periodDays * 0.25);
    if (today - sub.last.day > sub.periodDays + grace) {
      sub.notes.push(`списаний нет с ${formatDay(sub.last.day)} — похоже, уже отменена`);
      sub.active = false;
    }
    const store = (sub.service || {}).store;
    if (store === "apple") sub.notes.push("оплачена через Apple — отменять в настройках iPhone, а не в банке");
    else if (store === "google") sub.notes.push("оплачена через Google — отменять в Google Play");
  }

  // ---------- форматирование ----------

  function plural(n, one, few, many) {
    if (n % 10 === 1 && n % 100 !== 11) return one;
    if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return few;
    return many;
  }

  function money(value) {
    const fixed = (Math.round(value * 100) / 100).toFixed(2);
    let [int, frac] = fixed.split(".");
    const negative = int.startsWith("-");
    if (negative) int = int.slice(1);
    int = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    let text = (negative ? "-" : "") + int + "," + frac;
    if (text.endsWith(",00")) text = text.slice(0, -3);
    return `${text} ₽`;
  }

  function whereToCancel(sub) {
    const service = sub.service || {};
    if (service.store === "apple") return "Настройки iPhone → ваше имя → Подписки";
    if (service.store === "google") return "Google Play → профиль → Платежи и подписки → Подписки";
    if (service.site) return `в приложении или на сайте ${service.site} (если оплачивали через App Store или Google Play — там)`;
    return "в приложении или личном кабинете сервиса; если его нет — письменным заявлением исполнителю";
  }

  function toJson(result, warnings) {
    const item = (s) => ({
      name: s.name, key: s.key, service_id: (s.service || {}).id || null,
      period: s.period, charges: s.charges.length,
      last_charge: isoDay(s.last.day), last_amount: Math.round(s.last.amount * 100) / 100,
      monthly: Math.round(s.monthly * 100) / 100, yearly: Math.round(s.yearly * 100) / 100,
      next_expected: s.period !== "неизвестно" ? isoDay(s.nextExpected) : null,
      active: s.active, confidence: s.confidence, notes: s.notes,
    });
    return { date: isoDay(result.today), warnings, subscriptions: result.found.map(item), possible: result.maybe.map(item) };
  }

  function toCsv(result) {
    // Защита от формул в Excel: значение из выписки не должно начинаться с = + - @.
    const safe = (v) => (/^[=+\-@\t\r]/.test(String(v)) ? "'" + v : String(v));
    const esc = (v) => { v = safe(v); return /[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
    const lines = [["Подписка", "Период", "Последнее списание", "Сумма", "В месяц", "В год", "Активна", "Уверенность", "Заметки"]];
    for (const s of [...result.found, ...result.maybe]) {
      lines.push([s.name, s.period, formatDay(s.last.day), s.last.amount.toFixed(2), s.monthly.toFixed(2),
        s.yearly.toFixed(2), s.active ? "да" : "нет", s.confidence, s.notes.join("; ")]);
    }
    return lines.map((l) => l.map(esc).join(";")).join("\r\n") + "\r\n";
  }

  function analyze(files, servicesData, today) {
    const services = prepareServices(servicesData);
    let transactions = [], warnings = [];
    for (const file of files) {
      const { transactions: t, warnings: w } = readStatement(file.text, file.name);
      transactions = transactions.concat(t);
      warnings = warnings.concat(w);
    }
    if (!transactions.length) throw new Error("в выписках нет списаний");
    const result = findSubscriptions(transactions, services, today);
    result.warnings = warnings;
    return result;
  }

  return {
    analyze, decode, readStatement, findSubscriptions, prepareServices, parseAmount, parseDate,
    merchantKey, matchService, money, plural, whereToCancel, formatDay, isoDay, makeDay, toJson, toCsv,
  };
});

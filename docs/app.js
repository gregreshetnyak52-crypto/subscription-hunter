/* Интерфейс веб-версии. Все данные из выписки вставляются как текст (textContent), не как HTML. */
(function () {
  "use strict";
  const SH = window.SubHunter;
  const $ = (id) => document.getElementById(id);
  let current = null;

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.assign(node, props || {});
    for (const child of [].concat(children || [])) {
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function chip(text, kind) {
    return el("span", { className: "chip" + (kind ? " " + kind : ""), textContent: text });
  }

  function noteKind(note) {
    if (note.startsWith("оплачена через")) return "info";
    if (note.includes("уже отменена")) return "muted";
    return "";
  }

  function showError(message) {
    const box = $("error");
    box.textContent = message;
    box.style.display = message ? "block" : "none";
  }

  function updateSavings() {
    const picked = current.found.filter((s, i) => s.active && $("pick-" + i) && $("pick-" + i).checked);
    const box = $("savings");
    if (!picked.length) {
      box.textContent = "Отметьте подписки, которые хотите отменить, — посчитаю, сколько сэкономите.";
      return;
    }
    const yearly = picked.reduce((sum, s) => sum + s.yearly, 0);
    const n = picked.length;
    box.textContent = `Отменив ${n} ${SH.plural(n, "подписку", "подписки", "подписок")}, вы сэкономите ≈ ${SH.money(yearly)} в год (${SH.money(yearly / 12)} в месяц).`;
  }

  function render(result) {
    current = result;
    const active = result.found.filter((s) => s.active);
    const monthly = active.reduce((sum, s) => sum + s.monthly, 0);
    $("count").textContent = String(active.length);
    $("monthly").textContent = SH.money(monthly);
    $("yearly").textContent = SH.money(monthly * 12);
    $("asof").textContent = `Данные на ${SH.formatDay(result.today)}. Суммы «в год» — по последнему списанию.`;

    const warnings = $("warnings");
    warnings.replaceChildren(...result.warnings.map((w) => el("div", { className: "warning", textContent: "⚠️ " + w })));

    const rows = $("rows");
    rows.replaceChildren();
    if (!result.found.length) {
      rows.append(el("tr", {}, el("td", { colSpan: 5, textContent: "Регулярных списаний не найдено. Для надёжного поиска нужна выписка хотя бы за 3 месяца." })));
    }
    result.found.forEach((s, i) => {
      const box = el("input", { type: "checkbox", id: "pick-" + i, disabled: !s.active });
      box.setAttribute("aria-label", "Отменить " + s.name);
      box.addEventListener("change", updateSavings);
      const nameCell = el("td", {}, [
        el("div", { className: "name", textContent: s.name + (s.active ? "" : " (отменена?)") }),
        el("div", { className: "meta", textContent: `${SH.money(s.last.amount)} · ${s.period} · последнее ${SH.formatDay(s.last.day)}` }),
      ]);
      s.notes.forEach((n) => nameCell.append(chip(n, noteKind(n))));
      rows.append(el("tr", { className: s.active ? "" : "stopped" }, [
        el("td", {}, el("label", { className: "pick" }, box)),
        nameCell,
        el("td", { className: "hide-sm", textContent: s.period }),
        el("td", { className: "num hide-sm", textContent: SH.money(s.last.amount) }),
        el("td", { className: "num", textContent: SH.money(s.yearly) }),
      ]));
    });

    $("maybe-panel").style.display = result.maybe.length ? "" : "none";
    $("maybe").replaceChildren(...result.maybe.map((s) => el("tr", {}, [
      el("td", {}, [el("div", { className: "name", textContent: s.name }),
        el("div", { className: "meta", textContent: `${SH.formatDay(s.last.day)} · ${SH.money(s.last.amount)}` })]),
    ])));

    const toCancel = active.concat(result.maybe);
    $("cancel-panel").style.display = toCancel.length ? "" : "none";
    $("cancel").replaceChildren(...toCancel.map((s) => el("tr", {}, [
      el("td", { className: "name", textContent: s.name }),
      el("td", { textContent: SH.whereToCancel(s) }),
    ])));

    updateSavings();
    $("result").style.display = "block";
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function analyzeFiles(files) {
    showError("");
    try {
      render(SH.analyze(files, window.SUBHUNTER_SERVICES));
    } catch (e) {
      $("result").style.display = "none";
      showError(e.message + ". Проверьте, что это выписка в CSV с колонками даты, описания и суммы.");
    }
  }

  async function readFiles(fileList) {
    const files = [];
    for (const file of fileList) {
      files.push({ name: file.name, text: SH.decode(new Uint8Array(await file.arrayBuffer())) });
    }
    if (files.length) analyzeFiles(files);
  }

  function download(name, text, type) {
    const url = URL.createObjectURL(new Blob(["﻿" + text], { type }));
    const a = el("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const drop = $("drop");
  $("pick").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", (e) => readFiles(e.target.files));
  $("sample").addEventListener("click", () => analyzeFiles([{ name: "пример", text: window.SUBHUNTER_SAMPLE }]));
  $("download").addEventListener("click", () => current && download("subscriptions.csv", SH.toCsv(current), "text/csv;charset=utf-8"));
  $("reset").addEventListener("click", () => {
    $("result").style.display = "none";
    $("file").value = "";
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => readFiles(e.dataTransfer.files));
})();

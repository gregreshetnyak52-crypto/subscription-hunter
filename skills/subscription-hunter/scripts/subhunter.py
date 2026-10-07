#!/usr/bin/env python3
"""Subscription Hunter — находит подписки и регулярные списания в банковской выписке.

Работает локально, без интернета и без сторонних библиотек: выписка никуда
не отправляется. Понимает CSV-выписки российских банков — колонки
определяются по смыслу, разделитель и кодировка подбираются сами.

Примеры:
    python3 subhunter.py выписка.csv
    python3 subhunter.py сбер.csv тбанк.csv --format json
    python3 subhunter.py выписка.csv --today 2026-10-07
"""
import argparse
import csv
import io
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

SERVICES_PATH = Path(__file__).resolve().parent / "services.json"

# Период подписки: название, длина в днях, допуск в днях.
PERIODS = [
    ("неделя", 7, 1),
    ("месяц", 30.44, 4),
    ("квартал", 91.3, 7),
    ("полгода", 182.6, 10),
    ("год", 365.25, 12),
]

HEADER_KEYWORDS = {
    "date": ["дата операции", "дата транзакции", "дата", "date"],
    "amount": ["сумма операции", "сумма в валюте счета", "сумма в валюте счёта", "сумма", "amount"],
    "debit": ["расход", "списание", "debit", "дебет"],
    "credit": ["приход", "зачисление", "поступление", "credit", "кредит"],
    "description": ["описание", "назначение платежа", "назначение", "наименование", "merchant",
                    "контрагент", "получатель", "description", "details", "место"],
    "category": ["категория", "category", "mcc"],
    "status": ["статус", "status"],
}

FAILED_STATUSES = ("отклон", "failed", "declined", "отмен", "cancel")

NOISE_WORDS = re.compile(
    r"\b(оплата|покупка|списание|платеж|платёж|регулярный|подписка|подписки|оплата услуг|"
    r"retail|purchase|payment|card|карта|карты|rus|ru|msk|moscow|москва|moskva|g|г|spb|"
    r"www|com|sbp|сбп)\b"
)


@dataclass
class Transaction:
    day: date
    amount: float  # положительное число — сумма списания
    description: str
    category: str = ""


@dataclass
class Subscription:
    name: str
    key: str
    period: str
    period_days: float
    charges: list = field(default_factory=list)
    confidence: str = "высокая"
    service: dict = None
    notes: list = field(default_factory=list)
    active: bool = True

    @property
    def last(self):
        return self.charges[-1]

    @property
    def monthly(self):
        return self.last.amount * 30.44 / self.period_days

    @property
    def yearly(self):
        return self.monthly * 12

    @property
    def next_expected(self):
        return self.last.day + timedelta(days=round(self.period_days))


# ---------- чтение выписки ----------

def read_text(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def sniff_delimiter(text):
    sample = "\n".join(text.splitlines()[:30])
    counts = {d: sample.count(d) for d in (";", "\t", ",")}
    return max(counts, key=counts.get)


def find_column(header, kind, taken):
    names = [h.strip().lower() for h in header]
    for keyword in HEADER_KEYWORDS[kind]:
        for i, name in enumerate(names):
            if i in taken:
                continue
            if name == keyword or name.startswith(keyword):
                return i
    for keyword in HEADER_KEYWORDS[kind]:
        for i, name in enumerate(names):
            if i not in taken and keyword in name:
                return i
    return None


def map_columns(header):
    columns, taken = {}, set()
    for kind in ("date", "amount", "debit", "credit", "description", "category", "status"):
        index = find_column(header, kind, taken)
        if index is not None:
            columns[kind] = index
            taken.add(index)
    return columns


def parse_amount(value):
    if value is None:
        return None
    text = str(value).strip().replace(" ", "").replace(" ", "").replace("−", "-")
    text = re.sub(r"[^\d,.\-+()]", "", text)
    if not text or not re.search(r"\d", text):
        return None
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()+-")
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_date(value):
    text = str(value).strip().split()[0] if str(value).strip() else ""
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_statement(path):
    """Возвращает (список списаний, предупреждения)."""
    text = read_text(path)
    rows = list(csv.reader(io.StringIO(text), delimiter=sniff_delimiter(text)))
    header_index, columns = None, None
    for i, row in enumerate(rows[:40]):
        candidate = map_columns(row)
        if "date" in candidate and "description" in candidate and (
                "amount" in candidate or "debit" in candidate):
            header_index, columns = i, candidate
            break
    if header_index is None:
        raise ValueError(f"{path}: не нашёл строку заголовков с датой, суммой и описанием операции")

    warnings, parsed = [], []
    for row in rows[header_index + 1:]:
        if not row or all(not cell.strip() for cell in row):
            continue

        def cell(kind):
            index = columns.get(kind)
            return row[index] if index is not None and index < len(row) else ""

        if any(s in cell("status").lower() for s in FAILED_STATUSES):
            continue
        day = parse_date(cell("date"))
        if day is None:
            continue
        if "debit" in columns:
            debit = parse_amount(cell("debit"))
            amount = -abs(debit) if debit else None
            if amount is None and "amount" in columns:
                amount = parse_amount(cell("amount"))
        else:
            amount = parse_amount(cell("amount"))
        if amount is None:
            continue
        parsed.append((day, amount, cell("description").strip(), cell("category").strip()))

    if not parsed:
        raise ValueError(f"{path}: в выписке не нашлось операций")
    has_negative = any(a < 0 for _, a, _, _ in parsed)
    if not has_negative:
        warnings.append(f"{path}: в выписке нет отрицательных сумм — считаю все операции списаниями")
    transactions = [
        Transaction(day, abs(a), d, c) for day, a, d, c in parsed if (a < 0 or not has_negative) and a != 0
    ]
    return transactions, warnings


# ---------- поиск подписок ----------

def load_services(path=SERVICES_PATH):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for service in data["services"]:
        service["_re"] = [re.compile(p) for p in service["patterns"]]
    return data["services"]


def match_service(description, services):
    text = description.lower().replace("ё", "е")
    for service in services:
        if any(p.search(text) for p in service["_re"]):
            return service
    return None


def merchant_key(description):
    text = description.lower().replace("ё", "е")
    text = re.sub(r"\d{3,}", " ", text)
    text = re.sub(r"[^a-zа-я0-9\s]", " ", text)
    text = NOISE_WORDS.sub(" ", text)
    words = [w for w in text.split() if len(w) > 1]
    return " ".join(words[:3]) or description.strip().lower()


def pretty_name(charges):
    description = charges[-1].description.strip()
    return re.sub(r"\s+", " ", description)[:60] or "Без описания"


def fits_period(intervals, days, tolerance):
    """Доля интервалов, кратных периоду (пропуск месяца тоже считается)."""
    good = 0
    for interval in intervals:
        k = max(1, round(interval / days))
        if abs(interval - k * days) <= tolerance * k and k <= 3:
            good += 1
    return good / len(intervals)


def detect_period(intervals):
    best = None
    for name, days, tolerance in PERIODS:
        share = fits_period(intervals, days, tolerance)
        # При равной доле выбираем более длинный период: 366 дней — это год, а не два полугодия.
        if share >= 0.75 and (best is None or share >= best[2]):
            best = (name, days, share)
    return best


def amounts_consistent(charges):
    median = statistics.median(c.amount for c in charges)
    close = sum(1 for c in charges if abs(c.amount - median) <= 0.3 * median)
    return close / len(charges) >= 0.6


def find_subscriptions(transactions, services=None, today=None):
    services = services if services is not None else load_services()
    today = today or max(t.day for t in transactions)
    groups = {}
    for t in sorted(transactions, key=lambda t: t.day):
        service = match_service(t.description, services)
        key = f"service:{service['id']}" if service else merchant_key(t.description)
        groups.setdefault(key, {"service": service, "charges": []})["charges"].append(t)

    found, maybe = [], []
    for key, group in groups.items():
        charges, service = group["charges"], group["service"]
        # Несколько списаний в один день у одного продавца — это покупки, а не подписка.
        days = sorted({c.day for c in charges})
        if len(days) >= 2:
            intervals = [(b - a).days for a, b in zip(days, days[1:])]
            period = detect_period(intervals)
            if period and (amounts_consistent(charges) or service):
                per_day = {}
                for c in charges:
                    per_day[c.day] = c  # последнее списание дня
                ordered = [per_day[d] for d in days]
                sub = Subscription(
                    name=service["name"] if service else pretty_name(ordered),
                    key=key, period=period[0], period_days=period[1], charges=ordered,
                    service=service, confidence="высокая" if len(ordered) >= 3 else "средняя",
                )
                annotate(sub, today)
                found.append(sub)
                continue
        if service:
            sub = Subscription(name=service["name"], key=key, period="неизвестно",
                               period_days=30.44, charges=charges, service=service,
                               confidence="низкая")
            sub.notes.append("одно списание известного сервиса — проверьте, подписка ли это")
            maybe.append(sub)

    found.sort(key=lambda s: -s.yearly)
    return found, maybe


def annotate(sub, today):
    amounts = [c.amount for c in sub.charges]
    first, last = amounts[0], amounts[-1]
    later = statistics.median(amounts[1:])
    if len(amounts) >= 2 and first <= 0.1 * later:
        sub.notes.append(f"началась с пробного периода за {money(first)}")
        first = amounts[1]
    if first and last > first * 1.05:
        sub.notes.append(f"цена выросла на {round((last / first - 1) * 100)}%: было {money(first)}, стало {money(last)}")
    grace = max(5, sub.period_days * 0.25)
    if (today - sub.last.day).days > sub.period_days + grace:
        sub.notes.append(f"списаний нет с {sub.last.day:%d.%m.%Y} — похоже, уже отменена")
        sub.active = False
    else:
        sub.active = True
    store = (sub.service or {}).get("store")
    if store == "apple":
        sub.notes.append("оплачена через Apple — отменять в настройках iPhone, а не в банке")
    elif store == "google":
        sub.notes.append("оплачена через Google — отменять в Google Play")


# ---------- отчёт ----------

def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def money(value):
    rounded = round(value, 2)
    text = f"{rounded:,.2f}".replace(",", " ").replace(".", ",")
    if text.endswith(",00"):
        text = text[:-3]
    return f"{text} ₽"


CANCEL_GENERAL = (
    "Зайдите в приложение или на сайт сервиса и найдите раздел управления подпиской "
    "(обычно в профиле или настройках оплаты). Отключите автопродление и сохраните "
    "скриншот или письмо-подтверждение."
)
CANCEL_APPLE = "iPhone/iPad: Настройки → ваше имя → Подписки → выберите подписку → Отменить подписку."
CANCEL_GOOGLE = "Android: Google Play → значок профиля → Платежи и подписки → Подписки → выберите подписку → Отменить."
CANCEL_BANK = (
    "Если отменить не получается: в приложении банка поищите раздел подписок или "
    "автоплатежей, позвоните в банк или в крайнем случае перевыпустите карту — "
    "автосписания по старой карте прекратятся."
)


def where_to_cancel(sub):
    service = sub.service or {}
    if service.get("store") == "apple":
        return "Настройки iPhone → ваше имя → Подписки"
    if service.get("store") == "google":
        return "Google Play → профиль → Платежи и подписки → Подписки"
    if service.get("site"):
        return f"в приложении или на сайте {service['site']} (если оплачивали через App Store или Google Play — там)"
    return "в приложении или личном кабинете сервиса; если его нет — письменным заявлением исполнителю"


def report_markdown(found, maybe, warnings, today):
    active = [s for s in found if s.active]
    lines = []
    monthly = sum(s.monthly for s in active)
    lines.append(f"# Активных подписок: {len(active)} · ≈ {money(monthly)} в месяц · ≈ {money(monthly * 12)} в год")
    stopped = len(found) - len(active)
    if stopped:
        lines.append("")
        what = ("одна подписка, похоже, уже отменена" if stopped == 1
                else f"{stopped} {plural(stopped, 'подписка', 'подписки', 'подписок')}, похоже, уже отменены")
        lines.append(f"Ещё {what}: списаний давно не было. В таблице — с пометкой «отменена?».")
    lines.append("")
    lines.append(f"Данные на {today:%d.%m.%Y}. Суммы «в месяц» и «в год» — по последнему списанию.")
    for warning in warnings:
        lines.append(f"\n> ⚠️ {warning}")
    if found:
        lines += ["", "| Подписка | Период | Последнее списание | Сумма | В год | Уверенность | Заметки |",
                  "|---|---|---|---:|---:|---|---|"]
        for s in found:
            status = "" if s.active else " (отменена?)"
            lines.append(
                f"| {s.name}{status} | {s.period} | {s.last.day:%d.%m.%Y} | {money(s.last.amount)} | "
                f"{money(s.yearly)} | {s.confidence} | {'; '.join(s.notes) or '—'} |")
    else:
        lines += ["", "Регулярных списаний не найдено. Для надёжного поиска нужна выписка хотя бы за 3 месяца."]
    if maybe:
        lines += ["", "## Возможные подписки", "",
                  "Одно списание известного сервиса. Если это годовая подписка или новая — она появится здесь.", ""]
        for s in maybe:
            lines.append(f"- **{s.name}** — {s.last.day:%d.%m.%Y}, {money(s.last.amount)}")
    to_cancel = [s for s in found if s.active] + maybe
    if to_cancel:
        lines += ["", "## Как отменить", "", "| Подписка | Где отменять |", "|---|---|"]
        lines += [f"| {s.name} | {where_to_cancel(s)} |" for s in to_cancel]
        lines += ["", "Общий порядок:", "",
                  f"1. {CANCEL_GENERAL}",
                  f"2. Подписки, оформленные в приложении на телефоне, отменяются в магазине приложений. {CANCEL_APPLE} {CANCEL_GOOGLE}",
                  f"3. {CANCEL_BANK}", ""]
    lines.append("Списали после отмены или за неиспользованный период? Можно потребовать деньги назад: "
                 "ст. 32 Закона «О защите прав потребителей» позволяет отказаться от услуги, "
                 "оплатив только фактически понесённые расходы исполнителя.")
    return "\n".join(lines) + "\n"


def report_json(found, maybe, warnings, today):
    def item(s):
        return {
            "name": s.name, "key": s.key, "service_id": (s.service or {}).get("id"),
            "period": s.period, "charges": len(s.charges),
            "last_charge": s.last.day.isoformat(), "last_amount": round(s.last.amount, 2),
            "monthly": round(s.monthly, 2), "yearly": round(s.yearly, 2),
            "next_expected": s.next_expected.isoformat() if s.period != "неизвестно" else None,
            "active": s.active, "confidence": s.confidence, "notes": s.notes,
        }
    return json.dumps({"date": today.isoformat(), "warnings": warnings,
                       "subscriptions": [item(s) for s in found],
                       "possible": [item(s) for s in maybe]}, ensure_ascii=False, indent=2) + "\n"


def safe_cell(value):
    """Защита от формул в Excel: значение из выписки не должно начинаться с = + - @."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def report_csv(found, maybe, warnings, today):
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Подписка", "Период", "Последнее списание", "Сумма", "В месяц", "В год",
                     "Активна", "Уверенность", "Заметки"])
    for s in found + maybe:
        writer.writerow([safe_cell(s.name), s.period, s.last.day.strftime("%d.%m.%Y"), f"{s.last.amount:.2f}",
                         f"{s.monthly:.2f}", f"{s.yearly:.2f}",
                         "да" if s.active else "нет", s.confidence, safe_cell("; ".join(s.notes))])
    return out.getvalue()


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("statements", nargs="+", help="CSV-выписки (можно несколько счетов)")
    p.add_argument("--format", choices=["md", "json", "csv"], default="md", help="формат отчёта (по умолчанию md)")
    p.add_argument("--today", help="дата отчёта ГГГГ-ММ-ДД (по умолчанию — последняя дата в выписке)")
    p.add_argument("-o", "--output", help="сохранить отчёт в файл")
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    transactions, warnings = [], []
    for path in args.statements:
        try:
            items, notes = read_statement(path)
        except (OSError, ValueError) as exc:
            p.error(str(exc))
        transactions += items
        warnings += notes
    if not transactions:
        p.error("в выписках нет списаний")
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else max(t.day for t in transactions)
    found, maybe = find_subscriptions(transactions, today=today)
    render = {"md": report_markdown, "json": report_json, "csv": report_csv}[args.format]
    text = render(found, maybe, warnings, today)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Отчёт сохранён: {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

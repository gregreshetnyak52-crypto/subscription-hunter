"""Тесты Subscription Hunter: чтение выписок, поиск подписок, отчёты."""
import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "subscription-hunter" / "scripts" / "subhunter.py"
EXAMPLE = ROOT / "examples" / "vypiska-primer.csv"

spec = importlib.util.spec_from_file_location("subhunter", SCRIPT)
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)


def write(text, encoding="utf-8"):
    tmp = tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False)
    tmp.write(text.encode(encoding))
    tmp.close()
    return tmp.name


def tx(day, amount, description):
    return sh.Transaction(date.fromisoformat(day), amount, description)


def monthly(description, amount, months, day=10, year=2026):
    return [tx(f"{year}-{m:02d}-{day:02d}", amount, description) for m in months]


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = sh.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class ParseTest(unittest.TestCase):
    def test_amounts(self):
        cases = {"-1 234,56": -1234.56, "−399,00": -399, "1 990.00": 1990, "(150,00)": -150,
                 "+85 000,00": 85000, "1,234.50": 1234.5, "1.234,50": 1234.5, "": None, "abc": None}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(sh.parse_amount(text), expected)

    def test_dates(self):
        self.assertEqual(sh.parse_date("12.09.2026 14:03"), date(2026, 9, 12))
        self.assertEqual(sh.parse_date("2026-09-12"), date(2026, 9, 12))
        self.assertEqual(sh.parse_date("12.09.26"), date(2026, 9, 12))
        self.assertIsNone(sh.parse_date("вчера"))

    def test_cp1251_comma_and_preamble(self):
        text = ("Выписка по счёту 40817...\nПериод: 01.06.2026 - 30.09.2026\n\n"
                "Дата операции,Описание,Сумма операции,Статус\n"
                "12.06.2026,OKKO.TV,\"-399,00\",Выполнена\n"
                "13.06.2026,OKKO.TV,\"-399,00\",Отклонена\n")
        transactions, warnings = sh.read_statement(write(text, "cp1251"))
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].amount, 399)
        self.assertEqual(warnings, [])

    def test_separate_debit_and_credit_columns(self):
        text = ("Дата;Назначение платежа;Расход;Приход\n"
                "01.07.2026;Зарплата;;85000\n"
                "02.07.2026;SPOTIFY;169,00;\n")
        transactions, _ = sh.read_statement(write(text))
        self.assertEqual([(t.description, t.amount) for t in transactions], [("SPOTIFY", 169)])

    def test_all_positive_amounts_are_treated_as_expenses_with_warning(self):
        text = "Дата операции;Описание;Сумма\n01.07.2026;NETFLIX;799\n"
        transactions, warnings = sh.read_statement(write(text))
        self.assertEqual(transactions[0].amount, 799)
        self.assertTrue(warnings)

    def test_missing_header_is_an_error(self):
        with self.assertRaises(ValueError):
            sh.read_statement(write("a;b;c\n1;2;3\n"))


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.services = sh.load_services()

    def find(self, transactions, today=None):
        return sh.find_subscriptions(transactions, self.services, today)

    def test_monthly_known_service(self):
        found, maybe = self.find(monthly("YANDEX*PLUS MOSCOW", 399, range(4, 10)))
        self.assertEqual([s.name for s in found], ["Яндекс Плюс"])
        self.assertEqual(found[0].period, "месяц")
        self.assertAlmostEqual(found[0].yearly, 399 * 12)
        self.assertEqual(maybe, [])

    def test_unknown_merchant_with_ids_in_description(self):
        charges = [tx(f"2026-{m:02d}-01", 2900, f"FITNESS PRIME {1000 + m} MOSCOW") for m in range(4, 10)]
        found, _ = self.find(charges)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].confidence, "высокая")

    def test_irregular_purchases_are_not_subscriptions(self):
        days = [1, 2, 4, 5, 9, 11, 12, 16, 19, 20, 23, 27, 28]
        charges = [tx(f"2026-07-{d:02d}", 300 + d * 37, "PYATEROCHKA 1234") for d in days]
        found, maybe = self.find(charges)
        self.assertEqual(found, [])
        self.assertEqual(maybe, [])

    def test_regular_but_wildly_different_amounts_are_skipped(self):
        charges = [tx(f"2026-{m:02d}-05", a, "OZON MARKETPLACE") for m, a in zip(range(4, 10), [300, 4500, 120, 9800, 760, 2300])]
        self.assertEqual(self.find(charges)[0], [])

    def test_missed_month_still_counts(self):
        found, _ = self.find(monthly("SPOTIFY", 169, [4, 5, 7, 8, 9]))
        self.assertEqual(found[0].period, "месяц")

    def test_weekly_and_yearly(self):
        weekly = [tx(f"2026-07-{d:02d}", 99, "BOOSTY.TO") for d in (1, 8, 15, 22, 29)]
        self.assertEqual(self.find(weekly)[0][0].period, "неделя")
        yearly = [tx("2025-05-20", 2490, "LITRES.RU"), tx("2026-05-21", 2490, "LITRES.RU")]
        found, _ = self.find(yearly)
        self.assertEqual(found[0].period, "год")
        self.assertAlmostEqual(found[0].monthly, 2490 * 30.44 / 365.25)

    def test_quarterly_is_not_three_months(self):
        charges = [tx(d, 990, "AMEDIATEKA") for d in ("2026-01-10", "2026-04-11", "2026-07-11", "2026-10-10")]
        self.assertEqual(self.find(charges)[0][0].period, "квартал")

    def test_price_increase_trial_and_cancelled(self):
        charges = ([tx("2026-04-03", 1, "OKKO.TV")] + monthly("OKKO.TV", 299, [5, 6], day=3)
                   + monthly("OKKO.TV", 399, [7, 8], day=3))
        found, _ = self.find(charges, today=date(2026, 12, 1))
        notes = " ".join(found[0].notes)
        self.assertIn("пробного периода за 1 ₽", notes)
        self.assertIn("цена выросла на 33%", notes)
        self.assertIn("похоже, уже отменена", notes)
        self.assertFalse(found[0].active)

    def test_active_when_recent(self):
        found, _ = self.find(monthly("NETFLIX", 799, range(4, 10)), today=date(2026, 9, 20))
        self.assertTrue(found[0].active)

    def test_apple_gets_store_note(self):
        found, _ = self.find(monthly("APPLE.COM/BILL", 149, range(4, 10)))
        self.assertIn("отменять в настройках iPhone", " ".join(found[0].notes))
        self.assertIn("Настройки iPhone", sh.where_to_cancel(found[0]))

    def test_single_known_charge_is_possible_subscription(self):
        found, maybe = self.find([tx("2026-05-22", 2490, "LITRES.RU")])
        self.assertEqual(found, [])
        self.assertEqual(maybe[0].name, "Литрес")
        self.assertEqual(maybe[0].confidence, "низкая")

    def test_taxi_does_not_match_yandex_plus(self):
        self.assertIsNone(sh.match_service("YANDEX.GO MOSCOW RUS", self.services))
        self.assertEqual(sh.match_service("YANDEX*PLUS", self.services)["id"], "yandex-plus")

    def test_sorted_by_yearly_cost(self):
        charges = monthly("SPOTIFY", 169, range(4, 10)) + monthly("OPENAI *CHATGPT", 1990, range(4, 10))
        found, _ = self.find(charges)
        self.assertEqual([s.name for s in found], ["ChatGPT Plus", "Spotify"])


class ServicesTest(unittest.TestCase):
    def test_catalog_is_valid(self):
        data = json.loads((SCRIPT.parent / "services.json").read_text(encoding="utf-8"))
        ids = [s["id"] for s in data["services"]]
        self.assertEqual(len(ids), len(set(ids)))
        for service in data["services"]:
            with self.subTest(service=service["id"]):
                self.assertTrue(service["name"] and service["patterns"])
                for pattern in service["patterns"]:
                    re.compile(pattern)
                if service.get("site"):
                    self.assertNotRegex(service["site"], r"^https?://|/")


class ReportTest(unittest.TestCase):
    def test_example_statement(self):
        code, out, _ = run_cli(str(EXAMPLE))
        self.assertEqual(code, 0)
        self.assertIn("# Активных подписок: 5 · ≈ 5 837 ₽ в месяц · ≈ 70 044 ₽ в год", out)
        for name in ["FITNESS PRIME KLUB", "ChatGPT Plus", "Яндекс Плюс", "Okko", "Apple", "Иви (отменена?)"]:
            self.assertIn(name, out)
        self.assertIn("**Литрес**", out)
        self.assertNotIn("PYATEROCHKA", out)
        self.assertNotIn("COFFEE", out)
        self.assertNotIn("YANDEX.GO", out)

    def test_json_and_csv(self):
        code, out, _ = run_cli(str(EXAMPLE), "--format", "json")
        data = json.loads(out)
        self.assertEqual(len(data["subscriptions"]), 6)
        self.assertEqual(sum(1 for s in data["subscriptions"] if s["active"]), 5)
        code, out, _ = run_cli(str(EXAMPLE), "--format", "csv")
        self.assertEqual(len(out.strip().splitlines()), 1 + 6 + 1)

    def test_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.md"
            code, out, _ = run_cli(str(EXAMPLE), "-o", str(target))
            self.assertEqual(code, 0)
            self.assertIn("Активных подписок", target.read_text(encoding="utf-8"))

    def test_bad_file_is_a_clean_error(self):
        code, _, err = run_cli(write("a;b\n1;2\n"))
        self.assertEqual(code, 2)
        self.assertIn("не нашёл строку заголовков", err)

    def test_plural(self):
        forms = ("подписка", "подписки", "подписок")
        self.assertEqual([sh.plural(n, *forms) for n in (1, 3, 5, 11, 21, 24)],
                         ["подписка", "подписки", "подписок", "подписок", "подписка", "подписки"])


if __name__ == "__main__":
    unittest.main()

"""Тесты веб-версии: данные собраны, результат совпадает с Python, страница не ходит в сеть."""
import importlib.util
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SCRIPT = ROOT / "skills" / "subscription-hunter" / "scripts" / "subhunter.py"

spec = importlib.util.spec_from_file_location("build_web", ROOT / "scripts" / "build_web.py")
build_web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_web)

NODE_RUNNER = """
const fs = require("fs");
const SH = require(process.argv[2]);
const services = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const files = process.argv.slice(4).map((p) => ({ name: p, text: SH.decode(fs.readFileSync(p)) }));
const result = SH.analyze(files, services);
process.stdout.write(JSON.stringify(SH.toJson(result, result.warnings)));
"""


class DataTest(unittest.TestCase):
    def test_data_js_is_up_to_date(self):
        self.assertEqual((DOCS / "data.js").read_text(encoding="utf-8"), build_web.render(),
                         "Запустите python3 scripts/build_web.py")


class PrivacyTest(unittest.TestCase):
    def setUp(self):
        self.html = (DOCS / "index.html").read_text(encoding="utf-8")

    def test_csp_forbids_network(self):
        csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', self.html).group(1)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'none'", csp)
        self.assertIn("form-action 'none'", csp)
        self.assertIn("script-src 'self'", csp)

    def test_scripts_are_local(self):
        for src in re.findall(r'<script[^>]*src="([^"]+)"', self.html):
            with self.subTest(src=src):
                self.assertNotRegex(src, r"^(https?:)?//")
                self.assertTrue((DOCS / src).is_file())
        self.assertNotRegex(self.html, r"<script>(?!\s*</script>)")

    def test_no_network_apis_in_code(self):
        for name in ("subhunter.js", "app.js"):
            code = (DOCS / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotRegex(code, r"\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|innerHTML")


def statement(seed):
    r = random.Random(seed)
    names = ["YANDEX*PLUS", "OKKO.TV", "FITNESS 123456 MOSCOW", "OPENAI *CHATGPT", "APPLE.COM/BILL",
             "PYATEROCHKA 12", "COFFEE LIKE", "Онлайн-школа Знание", "AMEDIATEKA", "ТЕСТ ёлка"]
    rows = []
    start = date(2025, r.randint(1, 12), r.randint(1, 28))
    for name in r.sample(names, r.randint(3, len(names))):
        step = r.choice([7, 30, 91, 365, None])
        base = r.choice([1, 149, 169.5, 399, 1990, 2900])
        d = start + timedelta(days=r.randint(0, 20))
        for _ in range(r.randint(1, 8)):
            rows.append((d, name, base if r.random() < 0.8 else round(base * r.uniform(0.5, 1.6), 2)))
            d += timedelta(days=(step + r.randint(-3, 3)) if step else r.randint(1, 40))
    rows.sort()
    delim = r.choice([";", "\t"])
    lines = [delim.join(["Дата операции", "Описание", "Сумма операции"])]
    lines += [delim.join([d.strftime("%d.%m.%Y"), n, f"-{a:.2f}".replace(".", ",")]) for d, n, a in rows]
    return ("\n".join(lines) + "\n").encode(r.choice(["utf-8", "cp1251"]))


@unittest.skipUnless(shutil.which("node"), "нужен Node.js")
class ParityTest(unittest.TestCase):
    def compare(self, path):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as runner:
            runner.write(NODE_RUNNER)
        js = subprocess.run(["node", runner.name, str(DOCS / "subhunter.js"),
                             str(SCRIPT.parent / "services.json"), str(path)],
                            capture_output=True, text=True, check=True)
        py = subprocess.run([sys.executable, str(SCRIPT), str(path), "--format", "json"],
                            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(js.stdout), json.loads(py.stdout))

    def test_example_matches_python(self):
        self.compare(ROOT / "examples" / "vypiska-primer.csv")

    def test_random_statements_match_python(self):
        for seed in range(25):
            with self.subTest(seed=seed), tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as f:
                f.write(statement(seed))
                f.flush()
                self.compare(f.name)


class CsvSafetyTest(unittest.TestCase):
    def test_formula_cells_are_escaped(self):
        spec = importlib.util.spec_from_file_location("subhunter", SCRIPT)
        sh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sh)
        self.assertEqual(sh.safe_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(sh.safe_cell("Okko"), "Okko")


if __name__ == "__main__":
    unittest.main()

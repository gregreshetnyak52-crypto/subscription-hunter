"""Тесты проекта: пример отчёта актуален, ссылки рабочие, установщик и манифесты в порядке."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "subscription-hunter" / "scripts" / "subhunter.py"


class ExampleTest(unittest.TestCase):
    def test_example_report_is_up_to_date(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(ROOT / "examples" / "vypiska-primer.csv")],
                                capture_output=True, text=True, check=True)
        saved = (ROOT / "examples" / "otchet-primer.md").read_text(encoding="utf-8")
        self.assertEqual(result.stdout, saved, "Перегенерируйте examples/otchet-primer.md")

    def test_readme_headline_matches_report(self):
        headline = (ROOT / "examples" / "otchet-primer.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn(headline, (ROOT / "README.md").read_text(encoding="utf-8"))


class LinksTest(unittest.TestCase):
    def test_local_links_exist(self):
        for page in [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *ROOT.glob("skills/*/SKILL.md")]:
            text = page.read_text(encoding="utf-8")
            for link in re.findall(r"\]\(((?!https?://|\.\./\.\./issues|#)[^)]+)\)", text):
                with self.subTest(page=page.name, link=link):
                    self.assertTrue((page.parent / link).exists(), link)


class SkillTest(unittest.TestCase):
    def test_frontmatter(self):
        for skill in (ROOT / "skills").iterdir():
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            match = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text)
            with self.subTest(skill=skill.name):
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), skill.name)
                self.assertIn("Используй, когда", match.group(2))
                self.assertLessEqual(len(match.group(2)), 1024)
                self.assertRegex(text, r"(?i)не выдумывай")


class InstallTest(unittest.TestCase):
    def test_agent_targets(self):
        for agent, folder in {"claude": ".claude/skills", "codex": ".agents/skills", "cursor": ".cursor/skills"}.items():
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as home:
                subprocess.run(["sh", str(ROOT / "install.sh"), agent], env=dict(os.environ, HOME=home),
                               check=True, capture_output=True)
                skill = Path(home) / folder / "subscription-hunter"
                self.assertTrue((skill / "scripts" / "subhunter.py").is_file())
                self.assertTrue((skill / "scripts" / "services.json").is_file())
                self.assertFalse(list(skill.rglob("__pycache__")))

    def test_unknown_agent_is_rejected(self):
        result = subprocess.run(["sh", str(ROOT / "install.sh"), "vscode"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


class ManifestTest(unittest.TestCase):
    def test_names_match_readme(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], market["plugins"][0]["name"])
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"claude plugin install {plugin['name']}@{market['name']}", readme)


if __name__ == "__main__":
    unittest.main()

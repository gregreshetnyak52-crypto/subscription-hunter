#!/usr/bin/env python3
"""Собирает данные для веб-версии: docs/data.js из справочника сервисов и примера выписки.

Запускайте после изменения skills/subscription-hunter/scripts/services.json
или examples/vypiska-primer.csv. Тест tests/test_web.py проверяет, что файл актуален.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "skills" / "subscription-hunter" / "scripts" / "services.json"
SAMPLE = ROOT / "examples" / "vypiska-primer.csv"
TARGET = ROOT / "docs" / "data.js"


def render():
    services = json.loads(SERVICES.read_text(encoding="utf-8"))
    sample = SAMPLE.read_text(encoding="utf-8")
    return (
        "// Сгенерировано scripts/build_web.py — не редактируйте вручную.\n"
        f"window.SUBHUNTER_SERVICES = {json.dumps(services, ensure_ascii=False)};\n"
        f"window.SUBHUNTER_SAMPLE = {json.dumps(sample, ensure_ascii=False)};\n"
    )


if __name__ == "__main__":
    TARGET.write_text(render(), encoding="utf-8")
    print(f"Собрано: {TARGET.relative_to(ROOT)}")

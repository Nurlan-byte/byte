"""Generate or verify the GitHub preview of the C4 model using the pinned Structurizr release."""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
WAR_SHA256 = "ac1a2806e246364b88261a542a36ca7f3006ef48e5de68830afa8672f58690f1"
INTRO = """# Архитектура ежедневного заказа

Модель описывает приватную установку BakePlan. Кассу можно подключить для ежедневного импорта. Погода, внешний мониторинг и передача трасс включаются в настройках. При загрузке CSV администратору достаточно самого приложения.

Схемы получены из [модели C4](workspace.dsl). Нижняя схема прослеживает путь от кассы до рабочего места администратора. React работает в браузере, а планировщик и Chronos-2 работают внутри сервера API. SQLite хранит данные в файле на диске сервера.
"""
BUILD = """## Обновление схем

Скачайте [Structurizr 2026.09.19](https://download.structurizr.com/structurizr-2026.09.19.war). Из корня репозитория запустите команду с установленными Java 21 или новее и Python 3.14. Она проверит контрольную сумму инструмента, разберёт модель и обновит эту страницу.

```shell
python docs/architecture/render.py path/to/structurizr-2026.09.19.war
```

Параметр `--check` сверяет страницу с текущей моделью. [Документация Structurizr](https://docs.structurizr.com/export)
"""


def github_labels(diagram):
    # The exporter wraps even untitled diagrams in an invisible cluster. Let Mermaid apply the
    # requested direction to the real graph, including arrows that cross the system boundary.
    diagram = re.sub(r'  subgraph diagram \[" "\]\n    style diagram [^\n]+\n', "", diagram)
    diagram = diagram.rstrip().removesuffix("  end")
    # Keep names, technologies and links legible at README width. Full descriptions live in the DSL.
    diagram = re.sub(r"<div style='font-size: 80%; margin-top:10px'>.*?</div>", "", diagram)
    # The native export uses styled HTML divs that GitHub's Mermaid renderer filters.
    diagram = re.sub(r"<div[^>]*></div>", "", diagram)
    diagram = re.sub(r"</div><div[^>]*>", "<br/>", diagram)
    return re.sub(r"</?div[^>]*>", "", diagram).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("war", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with args.war.open("rb") as binary:
        if hashlib.file_digest(binary, "sha256").hexdigest() != WAR_SHA256:
            parser.exit(1, "Expected the official Structurizr 2026.09.19 WAR.\n")

    java = ["java", "-Dfile.encoding=UTF-8", "-jar", str(args.war.resolve())]
    workspace = ["-workspace", str(HERE / "workspace.dsl")]
    subprocess.run([*java, "validate", *workspace], check=True)
    sections = [INTRO]
    with tempfile.TemporaryDirectory() as output:
        subprocess.run([*java, "export", *workspace, "-format", "mermaid", "-output", output], check=True)
        for view, title in [("Context", "Окружение системы"), ("Containers", "Контейнеры и потоки данных")]:
            diagram = (Path(output) / f"structurizr-{view}.mmd").read_text(encoding="utf-8")
            sections.append(f"## {title}\n\n```mermaid\n{github_labels(diagram)}\n```\n")
    rendered = "\n".join([*sections, BUILD])
    page = HERE / "diagrams.md"
    if args.check:
        if not page.exists() or page.read_text(encoding="utf-8") != rendered:
            parser.exit(1, "C4 preview differs from workspace.dsl; regenerate diagrams.md.\n")
    else:
        page.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

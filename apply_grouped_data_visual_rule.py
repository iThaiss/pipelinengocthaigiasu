import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any


KEYWORDS = (
    "mau so lieu ghep nhom",
    "so lieu ghep nhom",
    "bang tan so ghep nhom",
)

TABLE_KEYWORDS = (
    "bang xet dau",
    "bang bien thien",
    "bang gia tri",
    "bang sau",
    "bang nhu sau",
    "bang tan so",
)


def normalize(value: str) -> str:
    value = value.lower().replace("đ", "d")
    return "".join(ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn")


def is_grouped_data(row: dict[str, Any]) -> bool:
    raw = row.get("raw_text")
    raw_item: dict[str, Any] = {}
    if raw:
        try:
            raw_item = json.loads(raw)
        except json.JSONDecodeError:
            raw_item = {}

    haystack = " ".join(
        str(value or "")
        for value in (
            row.get("question_text"),
            row.get("topic"),
            row.get("subtopic"),
            row.get("chapter"),
            raw_item.get("question_text"),
            raw_item.get("topic"),
            raw_item.get("subtopic"),
            raw_item.get("chapter"),
        )
    )
    normalized = normalize(haystack)
    return any(keyword in normalized for keyword in KEYWORDS)


def is_visual_table(row: dict[str, Any]) -> bool:
    raw = row.get("raw_text")
    raw_item: dict[str, Any] = {}
    if raw:
        try:
            raw_item = json.loads(raw)
        except json.JSONDecodeError:
            raw_item = {}
    haystack = " ".join(
        str(value or "")
        for value in (
            row.get("question_text"),
            raw_item.get("question_text"),
        )
    )
    normalized = normalize(haystack)
    return any(keyword in normalized for keyword in TABLE_KEYWORDS)


def apply_rule(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for row in data.get("questions", []):
        grouped_data = is_grouped_data(row)
        visual_table = is_visual_table(row)
        if not grouped_data and not visual_table:
            continue
        visual_type = "bang_so_lieu" if grouped_data else "bang_bien_thien"
        if not row.get("needs_visual") or row.get("visual_type") != visual_type:
            changed += 1
        row["needs_visual"] = True
        row["needs_review"] = True
        row["visual_type"] = visual_type
        raw = row.get("raw_text")
        if raw:
            try:
                raw_item = json.loads(raw)
                raw_item["needs_visual"] = True
                raw_item["visual_type"] = visual_type
                row["raw_text"] = json.dumps(raw_item, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path")
    args = parser.parse_args()
    changed = apply_rule(Path(args.json_path))
    print(changed)


if __name__ == "__main__":
    main()

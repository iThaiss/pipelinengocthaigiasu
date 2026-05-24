import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

from ingest_pipeline import (
    CLAUDE_API_KEY,
    CLAUDE_BASE_URL,
    PagePayload,
    build_page_payloads,
    normalize_anthropic_base_url,
    parse_page_with_claude,
    question_row,
)
from ingest_preview_html import render_html

DEFAULT_SOURCES = ["MAPSTUDY", "NGTIENDAT", "LB", "NGPHANTIEN"]


def log(message: str) -> None:
    print(message, flush=True)


def list_pdfs(source_dir: Path) -> list[Path]:
    return sorted(source_dir.rglob("*.pdf"))


def choose_pages(pdf_path: Path, rng: random.Random, pages_per_pdf: int) -> list[PagePayload]:
    payloads = build_page_payloads(str(pdf_path))
    if len(payloads) <= pages_per_pdf:
        return payloads
    return sorted(rng.sample(payloads, pages_per_pdf), key=lambda payload: payload.page_number)


def source_coverage(rows: list[dict[str, Any]]) -> set[str]:
    coverage: set[str] = set()
    for row in rows:
        q_type = row.get("question_type")
        if q_type:
            coverage.add(str(q_type))
        if row.get("needs_visual"):
            coverage.add("needs_visual")
    return coverage


def select_diverse_rows(rows: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_ids: set[int] = set()

    def add_first(predicate) -> None:
        for index, row in enumerate(rows):
            if index in used_ids:
                continue
            if predicate(row):
                selected.append(row)
                used_ids.add(index)
                return

    for q_type in ("multiple_choice", "true_false", "short_answer"):
        add_first(lambda row, q_type=q_type: row.get("question_type") == q_type and row.get("needs_visual"))
        add_first(lambda row, q_type=q_type: row.get("question_type") == q_type)

    add_first(lambda row: row.get("needs_visual"))

    for index, row in enumerate(rows):
        if len(selected) >= target:
            break
        if index not in used_ids:
            selected.append(row)
            used_ids.add(index)

    return selected[:target]


def parse_source(
    source_dir: Path,
    client: anthropic.Anthropic,
    rng: random.Random,
    target_questions: int,
    max_pdfs: int,
    pages_per_pdf: int,
) -> list[dict[str, Any]]:
    pdfs = list_pdfs(source_dir)
    rng.shuffle(pdfs)
    rows: list[dict[str, Any]] = []
    desired = {"multiple_choice", "true_false", "short_answer", "needs_visual"}

    for pdf_index, pdf_path in enumerate(pdfs[:max_pdfs], start=1):
        log(f"[{source_dir.name}] PDF {pdf_index}/{min(max_pdfs, len(pdfs))}: {pdf_path.name}")
        for payload in choose_pages(pdf_path, rng, pages_per_pdf):
            log(f"  - page {payload.page_number}")
            try:
                page_result = parse_page_with_claude(client, payload)
            except Exception as exc:
                log(f"    ! skip page: {exc}")
                continue

            for item_index, item in enumerate(page_result.get("items", []), start=1):
                if item.get("type") in {"theory", "example"}:
                    continue
                row = question_row(item, "dry-run", pdf_path.name, payload.page_number, image_url=None)
                row["source"] = source_dir.name
                row["source_path"] = str(pdf_path)
                row["sampled_at"] = datetime.now().isoformat()
                row["sample_page_item"] = item_index
                rows.append(row)

            coverage = source_coverage(rows)
            if len(rows) >= target_questions and desired.issubset(coverage):
                return rows

        coverage = source_coverage(rows)
        if len(rows) >= target_questions and {"multiple_choice", "short_answer", "needs_visual"}.issubset(coverage):
            # True/false is not present in every random slice; keep the preview moving once the common forms are covered.
            return rows

    return rows


def write_combined_json(path: Path, rows: list[dict[str, Any]], seed: int) -> None:
    path.write_text(
        json.dumps(
            {
                "filename": "Random source sample",
                "source": "MAPSTUDY + NGTIENDAT + LB + NGPHANTIEN",
                "seed": seed,
                "questions": rows,
                "failures": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Randomly scan PDF pages from source folders and render an HTML preview.")
    parser.add_argument("--database", default="D:/Database")
    parser.add_argument("--sources", nargs="+", default=DEFAULT_SOURCES)
    parser.add_argument("--target-per-source", type=int, default=8)
    parser.add_argument("--max-pdfs-per-source", type=int, default=4)
    parser.add_argument("--pages-per-pdf", type=int, default=2)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", default="artifacts/previews/random_sources_preview.html")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1, 10_000_000)
    rng = random.Random(seed)
    base_url = normalize_anthropic_base_url(CLAUDE_BASE_URL)
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY or "local-9router", base_url=base_url)
    database = Path(args.database)

    log(f"Seed: {seed}")
    log(f"Router: {base_url}")

    rows: list[dict[str, Any]] = []
    for source in args.sources:
        source_dir = database / source
        if not source_dir.exists():
            log(f"[{source}] missing folder: {source_dir}")
            continue
        source_rows = parse_source(
            source_dir=source_dir,
            client=client,
            rng=rng,
            target_questions=args.target_per_source,
            max_pdfs=args.max_pdfs_per_source,
            pages_per_pdf=args.pages_per_pdf,
        )
        log(f"[{source}] collected {len(source_rows)} questions; coverage={sorted(source_coverage(source_rows))}")
        rows.extend(select_diverse_rows(source_rows, args.target_per_source))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output.with_suffix(".json")
    write_combined_json(json_path, rows, seed)
    render_html(json.loads(json_path.read_text(encoding="utf-8")), output)
    log(str(output.resolve()))
    log(str(json_path.resolve()))


if __name__ == "__main__":
    main()

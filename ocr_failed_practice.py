"""OCR image-only practice PDFs and merge recoverable questions into source data.

Run with the OCR virtualenv created for rapidocr-onnxruntime:
    .venv_ocr/bin/python ocr_failed_practice.py --root local_curriculum_english
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR

import scan_practice_meta as sp


DEFAULT_ROOT = Path("local_curriculum_english")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def ocr_pdf_text(path: Path, cache_path: Path, scale: float = 1.8) -> tuple[str, int]:
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data.get("text", ""), int(data.get("page_count") or 0)
    ocr = RapidOCR()
    pages: list[str] = []
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            result, _elapsed = ocr(image)
            lines: list[str] = []
            if result:
                result = sorted(result, key=lambda row: (min(p[1] for p in row[0]), min(p[0] for p in row[0])))
                lines = [str(row[1]) for row in result]
            pages.append(f"[Page {page_index}]\n" + "\n".join(lines))
    text = "\n\n".join(pages)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"source_pdf": str(path), "page_count": len(pages), "text": text}, ensure_ascii=False, indent=2), encoding="utf-8")
    return text, len(pages)


def question_key(q: dict[str, Any]) -> tuple[Any, ...]:
    opts = q.get("options") or {}
    opt_sig = "|".join(f"{k}:{' '.join(str(opts[k]).split())}" for k in sorted(opts))[:600]
    text_sig = " ".join((q.get("question_text") or "").split())[:240]
    return (q.get("file_sha1"), q.get("question_number"), opt_sig or text_sig)


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--scale", type=float, default=1.8)
    args = parser.parse_args()
    root = args.root

    source_path = root / "output_json" / "practice_questions_no_vip90_source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    taxonomy = json.loads((root / "output_json" / "english_taxonomy_v2.json").read_text(encoding="utf-8"))
    nodes_path = root / "output_json" / "learning_map_nodes.json"
    node_index = sp.build_node_index(json.loads(nodes_path.read_text(encoding="utf-8")).get("nodes", [])) if nodes_path.exists() else {}

    failed = [item for item in source.get("files", []) if item.get("status") != "ok" and item.get("error") == "ocr_required_or_image_only_pdf"]
    cache_dir = root / "cache" / "ocr_text_rapidocr"
    ocr_results: list[dict[str, Any]] = []
    start = time.time()
    for index, result in enumerate(failed, start=1):
        file_info = result.get("file", {})
        rel = file_info.get("relative_path") or ""
        path = root / "input_sources" / rel
        sha1 = sp.file_sha1(path) if path.exists() else ""
        cache_path = cache_dir / f"{sha1 or index}.json"
        if not path.exists():
            ocr_result = sp.error_result(file_info, sha1, f"missing_pdf: {path}")
        else:
            text, page_count = ocr_pdf_text(path, cache_path, args.scale)
            hints = sp.make_hints(text, file_info, page_count, len(text))
            hints["text_quality"] = "ocr"
            questions = sp.regex_extract_questions(text, file_info, sha1, hints, taxonomy, node_index)
            ocr_result = {
                "file": file_info,
                "file_sha1": sha1,
                "scanned_at": datetime.now().isoformat(timespec="seconds"),
                "status": "ok" if questions else "failed",
                "needs_ai_review": True,
                "error": "" if questions else "ocr_regex_returned_zero_questions",
                "hints": hints,
                "file_summary": {"confidence": "medium" if questions else "low", "review_reason": "rapidocr_regex"},
                "questions": questions,
                "from_cache": cache_path.exists(),
                "ocr_cache": str(cache_path),
            }
        ocr_results.append(ocr_result)
        accepted, rejected = sp.split_questions([ocr_result])
        print(
            f"[{index}/{len(failed)}] raw={len(ocr_result.get('questions', []))} accepted={len(accepted)} "
            f"rejected={len(rejected)} status={ocr_result.get('status')} {Path(rel).name[:80]}",
            flush=True,
        )

    accepted_ocr, rejected_ocr = sp.split_questions(ocr_results)
    ocr_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ocr_engine": "rapidocr-onnxruntime",
        "total_files": len(ocr_results),
        "total_questions": len(accepted_ocr),
        "total_rejected_questions": len(rejected_ocr),
        "files": ocr_results,
        "questions": accepted_ocr,
        "rejected_questions": rejected_ocr,
    }
    ocr_path = root / "output_json" / "practice_questions_ocr_rescan.json"
    ocr_path.write_text(json.dumps(ocr_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    source_questions = [q for q in source.get("questions", []) if q.get("source_type") != "generated_backfill"]
    merged_by_key = {question_key(q): q for q in source_questions}
    for q in accepted_ocr:
        item = dict(q)
        item["source_type"] = "ocr_scanned_pdf"
        item["needs_review"] = True
        reason = item.get("review_reason") or ""
        item["review_reason"] = "; ".join(sorted(set([part for part in reason.split("; ") if part] + ["rapidocr_extracted"])))
        merged_by_key[question_key(item)] = item
    merged_questions = list(merged_by_key.values())

    ocr_by_path = {r.get("file", {}).get("relative_path"): r for r in ocr_results}
    new_files = []
    for item in source.get("files", []):
        rel = item.get("file", {}).get("relative_path")
        if rel in ocr_by_path and ocr_by_path[rel].get("status") == "ok":
            new_files.append(ocr_by_path[rel])
        else:
            new_files.append(item)

    improved = dict(source)
    improved["generated_at"] = datetime.now().isoformat(timespec="seconds")
    improved["ocr_merge_source"] = "rapidocr_failed_practice_merge"
    improved["ocr_questions_added"] = len(accepted_ocr)
    improved["questions"] = merged_questions
    improved["total_questions"] = len(merged_questions)
    improved["files"] = new_files
    improved["rejected_questions"] = [q for q in source.get("rejected_questions", []) if q.get("relative_path") not in ocr_by_path] + rejected_ocr
    improved["total_rejected_questions"] = len(improved["rejected_questions"])
    improved["passages"] = sp.build_passages([{"questions": merged_questions}])
    improved["total_passages"] = len(improved["passages"])
    out = root / "output_json" / "practice_questions_no_vip90_source_ocr.json"
    out.write_text(json.dumps(improved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {ocr_path}")
    print(f"Saved: {out}")
    print(f"OCR accepted questions: {len(accepted_ocr)}")
    print(f"OCR rejected questions: {len(rejected_ocr)}")
    print(f"Elapsed seconds: {time.time() - start:.1f}")


if __name__ == "__main__":
    main()

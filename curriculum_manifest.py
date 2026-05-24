import argparse
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum")
SUPPORTED_EXTENSIONS = {".pdf"}


@dataclass
class CurriculumFile:
    source: str
    order: int
    relative_path: str
    folder_path: str
    file_name: str
    stem: str
    extension: str
    size_bytes: int
    depth: int
    path_parts: list[str]
    sort_key: list[Any]


def natural_sort_key(value: str) -> list[Any]:
    parts = re.split(r"(\d+)", value.casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def path_sort_key(path: Path) -> list[Any]:
    key: list[Any] = []
    for part in path.parts:
        key.extend(natural_sort_key(part))
        key.append("")
    return key


def scan_source(source_dir: Path) -> list[CurriculumFile]:
    files = [
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS
    ]
    files = sorted(files, key=lambda path: path_sort_key(path.relative_to(source_dir)))

    rows: list[CurriculumFile] = []
    for index, path in enumerate(files, start=1):
        relative = path.relative_to(source_dir)
        folder = relative.parent
        rows.append(
            CurriculumFile(
                source=source_dir.name,
                order=index,
                relative_path=relative.as_posix(),
                folder_path="" if folder == Path(".") else folder.as_posix(),
                file_name=path.name,
                stem=path.stem,
                extension=path.suffix.casefold(),
                size_bytes=path.stat().st_size,
                depth=len(relative.parts) - 1,
                path_parts=list(relative.parts),
                sort_key=path_sort_key(relative),
            )
        )
    return rows


def build_manifest(root: Path) -> dict[str, Any]:
    input_root = root / "input_sources"
    sources = [path for path in input_root.iterdir() if path.is_dir()]
    sources = sorted(sources, key=lambda path: natural_sort_key(path.name))

    all_files: list[CurriculumFile] = []
    source_summaries: list[dict[str, Any]] = []
    for source_dir in sources:
        rows = scan_source(source_dir)
        all_files.extend(rows)
        folders = sorted({row.folder_path for row in rows if row.folder_path})
        source_summaries.append(
            {
                "source": source_dir.name,
                "file_count": len(rows),
                "folder_count": len(folders),
                "total_size_bytes": sum(row.size_bytes for row in rows),
                "folders": folders,
            }
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root.resolve()),
        "input_root": str(input_root.resolve()),
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "source_count": len(sources),
        "file_count": len(all_files),
        "total_size_bytes": sum(row.size_bytes for row in all_files),
        "sources": source_summaries,
        "files": [asdict(row) for row in all_files],
    }


def render_html(manifest: dict[str, Any]) -> str:
    source_cards = []
    for source in manifest["sources"]:
        folders = "".join(f"<li>{html.escape(folder)}</li>" for folder in source["folders"])
        source_cards.append(
            f"""
            <section class="source">
              <h2>{html.escape(source["source"])}</h2>
              <div class="metrics">
                <span>{source["file_count"]} PDFs</span>
                <span>{source["folder_count"]} folders</span>
                <span>{source["total_size_bytes"] / 1024 / 1024:.1f} MB</span>
              </div>
              <ol>{folders}</ol>
            </section>
            """
        )

    rows = []
    for item in manifest["files"]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['source'])}</td>"
            f"<td>{item['order']}</td>"
            f"<td>{html.escape(item['folder_path'])}</td>"
            f"<td>{html.escape(item['file_name'])}</td>"
            f"<td>{item['size_bytes'] / 1024 / 1024:.1f} MB</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Curriculum Manifest</title>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f7f7f5;
      color: #1d1d1b;
    }}
    header {{
      padding: 24px 32px;
      background: #ffffff;
      border-bottom: 1px solid #deded8;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    main {{
      padding: 24px 32px 40px;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .metric, .source {{
      background: #ffffff;
      border: 1px solid #deded8;
      border-radius: 6px;
      padding: 16px;
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
      margin-bottom: 4px;
    }}
    .sources {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .source h2 {{
      margin: 0 0 10px;
      font-size: 18px;
    }}
    .metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .metrics span {{
      padding: 4px 8px;
      background: #eef1ef;
      border-radius: 4px;
      font-size: 13px;
    }}
    ol {{
      margin: 0;
      padding-left: 22px;
      max-height: 220px;
      overflow: auto;
    }}
    li {{
      margin: 4px 0;
      font-size: 13px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #ffffff;
      border: 1px solid #deded8;
      border-radius: 6px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #ededE8;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #ecece6;
      z-index: 1;
    }}
    tbody tr:hover {{
      background: #fafaf7;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Curriculum Manifest</h1>
    <div>Generated at {html.escape(manifest["generated_at"])} from {html.escape(manifest["input_root"])}</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><strong>{manifest["source_count"]}</strong>Sources</div>
      <div class="metric"><strong>{manifest["file_count"]}</strong>PDF files</div>
      <div class="metric"><strong>{manifest["total_size_bytes"] / 1024 / 1024:.1f}</strong>Total MB</div>
    </section>
    <section class="sources">
      {''.join(source_cards)}
    </section>
    <table>
      <thead>
        <tr>
          <th>Source</th>
          <th>Order</th>
          <th>Folder</th>
          <th>File</th>
          <th>Size</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a local manifest for curriculum source PDFs.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Local curriculum root folder.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root)
    manifest = build_manifest(root)

    output_json = root / "output_json" / "curriculum_manifest.json"
    output_html = root / "previews" / "curriculum_manifest.html"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_html.write_text(render_html(manifest), encoding="utf-8")

    print(f"Wrote {output_json.resolve()}")
    print(f"Wrote {output_html.resolve()}")


if __name__ == "__main__":
    main()

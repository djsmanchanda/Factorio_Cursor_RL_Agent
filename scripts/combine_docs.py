# Path: scripts/combine_docs.py
# Purpose: Build FULL_DOCUMENTATION.md from active documentation while excluding archives.

from pathlib import Path


def active_markdown(docs_dir: Path) -> list[Path]:
    """Return active docs in stable path order; archived designs are intentionally omitted."""
    return sorted(
        path
        for path in docs_dir.rglob("*.md")
        if "archive" not in path.relative_to(docs_dir).parts
    )


def combine_markdown_files(
    output_file: str = "FULL_DOCUMENTATION.md",
    docs_dir: str = "docs",
    readme_file: str = "README.md",
) -> None:
    sources: list[Path] = []
    readme = Path(readme_file)
    if readme.exists():
        sources.append(readme)

    docs = Path(docs_dir)
    if docs.exists():
        sources.extend(active_markdown(docs))

    sections = [
        f"--- FILE: {path.as_posix()} ---\n\n{path.read_text(encoding='utf-8').rstrip()}"
        for path in sources
    ]
    Path(output_file).write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    print(f"Successfully combined {len(sources)} files into {output_file}")


if __name__ == "__main__":
    combine_markdown_files()

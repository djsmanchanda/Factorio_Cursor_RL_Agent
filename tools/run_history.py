# Path: tools/run_history.py
# Purpose: Rebuildable offline SQLite index of run packets for bounded cross-run retrieval.

from __future__ import annotations

import argparse
from datetime import timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_context import _build_context_lines, _clip
from tools.run_log_format import is_run_end_line, is_run_start_line, parse_timed_run_log_line


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS sources (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence (identity TEXT PRIMARY KEY, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
            identity TEXT PRIMARY KEY, started_at TEXT NOT NULL, header TEXT NOT NULL,
            episode TEXT, blocker_code TEXT, selected_task TEXT, status TEXT NOT NULL,
            packet TEXT NOT NULL, source TEXT NOT NULL, start_line INTEGER NOT NULL,
            content_size INTEGER NOT NULL
        );
    """)
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS packets_fts USING fts5(identity UNINDEXED, packet)")
    except sqlite3.OperationalError:
        pass  # Ordinary SQL remains available on builds without FTS5.
    return connection


def index_log(db_path: Path, log_path: Path) -> int:
    """Index actual run boundaries; return processed count (zero for unchanged source)."""
    if db_path.resolve() == log_path.resolve():
        raise ValueError("database must not overwrite the log")
    raw = log_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    source = str(log_path.resolve())
    lines = raw.decode("utf-8", errors="replace").splitlines()
    starts = [(number, parsed) for number, line in enumerate(lines)
              if is_run_start_line(line)
              and (parsed := parse_timed_run_log_line(line, run_started_at=None)) is not None
              and parsed.message.startswith("RUN START:")]
    connection = _connect(db_path)
    try:
        with connection:
            previous = connection.execute("SELECT sha256 FROM sources WHERE path=?", (source,)).fetchone()
            if previous is not None and previous["sha256"] == digest:
                return 0
            for index, (start, parsed) in enumerate(starts):
                stop = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
                chunk = lines[start:stop]
                identity = (parsed.timestamp.astimezone(timezone.utc).isoformat()
                            if parsed.timestamp.tzinfo else parsed.timestamp.isoformat())
                episode_match = re.search(r"\bepisode-[A-Za-z0-9_-]+", "\n".join(chunk))
                episode = episode_match.group(0) if episode_match else None
                blocker = {}
                for line in chunk:
                    timed = parse_timed_run_log_line(line, run_started_at=parsed.timestamp)
                    if timed is None or not timed.message.startswith("BLOCKER:"):
                        continue
                    try:
                        candidate = json.loads(timed.message.removeprefix("BLOCKER:").strip())
                    except ValueError:
                        continue
                    if isinstance(candidate, dict):
                        blocker = candidate
                details = blocker.get("details")
                task = details.get("selected_task") if isinstance(details, dict) else None
                code = blocker.get("code")
                ended = any(is_run_end_line(line) and
                            (line.strip() == "RUN END" or
                             ((end := parse_timed_run_log_line(line, run_started_at=parsed.timestamp))
                              is not None and end.message == "RUN END")) for line in chunk)
                status = "completed" if ended else "partial"
                size = sum(len(line) + 1 for line in chunk)
                existing = connection.execute("SELECT content_size, status FROM runs WHERE identity=?", (identity,)).fetchone()
                # An older archive must not replace a later, more complete observation.
                if existing and (existing["content_size"] > size or (existing["status"] == "completed" and status == "partial")):
                    continue
                packet = _build_context_lines(chunk, Path(source), first_line=start + 1)
                connection.execute("""INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(identity) DO UPDATE SET
                        header=excluded.header, episode=excluded.episode,
                        blocker_code=excluded.blocker_code, selected_task=excluded.selected_task,
                        status=excluded.status, packet=excluded.packet, source=excluded.source,
                        start_line=excluded.start_line, content_size=excluded.content_size""",
                    (identity, identity, chunk[0], episode, str(code) if code is not None else None,
                     str(task) if task is not None else None, status, packet, source, start + 1, size))
                raw_evidence = "\n".join(
                    f"{source}:{start + i + 1}: {line}" for i, line in enumerate(chunk)
                    if "OPENCODE HELPER:" not in line and "HELPER AGENT:" not in line
                )
                connection.execute("INSERT INTO evidence VALUES (?, ?) ON CONFLICT(identity) DO UPDATE SET text=excluded.text", (identity, raw_evidence))
                try:
                    connection.execute("DELETE FROM packets_fts WHERE identity=?", (identity,))
                    connection.execute("INSERT INTO packets_fts(identity, packet) VALUES (?, ?)", (identity, raw_evidence))
                except sqlite3.OperationalError:
                    pass
            connection.execute("INSERT INTO sources VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256", (source, digest))
        return len(starts)
    finally:
        connection.close()


def search(db_path: Path, query: str, limit: int = 5) -> str:
    """Search indexed raw evidence; emit bounded cited excerpts."""
    heading = "RUN HISTORY — rebuildable cache; verify cited raw evidence. completed means RUN END, not mission success.\n"
    if not db_path.exists():
        return heading + "UNKNOWN: history index does not exist.\n"
    limit = max(1, min(int(limit), 20))
    terms = re.findall(r"[\w-]+", query)[:20]
    if not terms:
        return heading + "No searchable terms.\n"
    connection = _connect(db_path)
    try:
        expression = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        try:
            matches = connection.execute("""SELECT runs.*, evidence.text AS raw_evidence FROM packets_fts JOIN runs USING(identity) JOIN evidence USING(identity)
                WHERE packets_fts MATCH ? ORDER BY rank, started_at DESC LIMIT ?""", (expression, limit)).fetchall()
        except sqlite3.OperationalError:
            clauses = " AND ".join("evidence.text LIKE ? ESCAPE '\\'" for _ in terms)
            patterns = ["%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for term in terms]
            matches = connection.execute(f"SELECT runs.*, evidence.text AS raw_evidence FROM runs JOIN evidence USING(identity) WHERE {clauses} ORDER BY started_at DESC LIMIT ?", (*patterns, limit)).fetchall()
    finally:
        connection.close()
    if not matches:
        return heading + "No matching indexed runs (not proof of no historical occurrence).\n"
    output = [heading]
    budget = min(2200, (11_500 - len(heading)) // len(matches))
    for row in matches:
        matching_lines = [line for line in row["raw_evidence"].splitlines() if any(term.lower() in line.lower() for term in terms)]
        entry = (f"run={row['identity']} episode={row['episode'] or 'UNKNOWN'} status={row['status']}\n"
                 f"terminal={_clip(row['blocker_code'] or 'UNKNOWN', 120)} task={_clip(row['selected_task'] or 'UNKNOWN', 160)}\n"
                 f"source={row['source']}:{row['start_line']}\n"
                 + "\n".join(_clip(line, 650) for line in list(dict.fromkeys(matching_lines[:1] + matching_lines[-2:]))))
        output.append(_clip(entry, budget))
    output.append("Bounded matches and snippets; omitted evidence remains in the cited raw logs.\n")
    return _clip("\n\n".join(output), 12_000)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index or search bounded offline run packets.")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("--db", type=Path, required=True)
    index.add_argument("--log", type=Path, action="append", required=True)
    lookup = commands.add_parser("search")
    lookup.add_argument("--db", type=Path, required=True)
    lookup.add_argument("--query", required=True)
    lookup.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.command == "index":
        for log in args.log:
            print(f"{log}: indexed {index_log(args.db, log)} run(s)")
    else:
        print(search(args.db, args.query, args.limit), end="")


if __name__ == "__main__":
    main()

# Path: tools/run_mod_stamping.py | Purpose: Reuse exact GUI artifacts across Python-only revisions and stamp changed runtime mods.
# Purpose: Make a live run's server mods joinable from the GUI: reuse an identical GUI copy, else give the run's
# copy a unique version and install it beside the GUI's own copies (Factorio's "sync mods" then picks it).
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

PROJECT_MODS = ("factorio_cursor_rl_agent", "factorio_laya_agent", "factorio_training_lab")


def content_hash(folder: Path, *, ignore_version: bool = False) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        content = path.read_bytes()
        if ignore_version and path.name == "info.json" and path.parent == folder:
            metadata = json.loads(content)
            metadata.pop("version", None)
            content = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        digest.update(str(path.relative_to(folder)).encode() + b"\0" + content)
    return digest.hexdigest()


def gui_copies(gui: Path, name: str) -> list[Path]:
    return [p for p in gui.iterdir() if p.is_dir() and (p.name == name or p.name.startswith(name + "_"))
            and (p / "info.json").is_file() and json.loads((p / "info.json").read_text())["name"] == name]


def pin_default(gui: Path, name: str, version: str) -> None:
    """Keep the GUI's existing copy as its default once several versions sit side by side."""
    path = gui / "mod-list.json"
    data = json.loads(path.read_text())
    for entry in data["mods"]:
        if entry["name"] == name and "version" not in entry:
            entry["version"] = version
            path.write_text(json.dumps(data, indent=2) + "\n")


def stamp(server_mods: Path, gui: Path, tag: int) -> dict[str, str]:
    result = {}
    for name in PROJECT_MODS:
        run_copy = server_mods / name
        info = json.loads((run_copy / "info.json").read_text())
        copies = gui_copies(gui, name)
        same = next((p for p in copies if content_hash(p, ignore_version=True) == content_hash(run_copy, ignore_version=True)), None)
        if same is not None:
            # A stamp changes metadata version, not gameplay source. Restore
            # the exact matching GUI metadata so the wire checksum also agrees.
            shutil.copyfile(same / "info.json", run_copy / "info.json")
            info = json.loads((run_copy / "info.json").read_text())
            if content_hash(run_copy) != content_hash(same):
                raise ValueError(f"GUI artifact reuse did not preserve exact content: {same}")
            result[name] = f"{info['version']} (matches GUI {same.name})"
            continue
        default = next((p for p in copies if p.name == name), None)
        major, minor, _ = info["version"].split(".")
        info["version"] = f"{major}.{minor}.{tag}"
        (run_copy / "info.json").write_text(json.dumps(info, indent=2) + "\n")
        target = gui / f"{name}_{info['version']}"
        if target.exists() and content_hash(target) != content_hash(run_copy):
            raise SystemExit(f"{target} exists with different content; choose another tag")
        if not target.exists():
            shutil.copytree(run_copy, target)
        if default is not None:
            pin_default(gui, name, json.loads((default / "info.json").read_text())["version"])
        result[name] = f"{info['version']} (installed {target.name})"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-mods", type=Path, required=True)
    parser.add_argument("--gui-mods", type=Path, default=Path.home() / ".factorio/mods")
    parser.add_argument("--tag", type=int, required=True, help="patch version for a non-matching run copy (<65536)")
    args = parser.parse_args()
    if not 0 < args.tag < 65536:
        raise SystemExit("tag must be 1..65535")
    print(json.dumps(stamp(args.server_mods, args.gui_mods, args.tag)))

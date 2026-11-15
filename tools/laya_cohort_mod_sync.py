# Path: tools/laya_cohort_mod_sync.py | Purpose: Copy verified run mods to the requested Starship GUI with reversible replacement.
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import uuid


def make_archive(mods, expected, output):
    mods, output = Path(mods), Path(output)
    files = {}
    with tarfile.open(output, 'w:gz') as archive:
        for path in sorted(mods.rglob('*')):
            if path.is_symlink():
                raise ValueError('Server mods cannot contain symlinks')
            if not path.is_file():
                continue
            relative = path.relative_to(mods).as_posix()
            data = path.read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            if (relative in expected and expected[relative] != checksum) or (relative not in expected and relative not in {'mod-list.json', 'mod-settings.dat'}):
                raise ValueError('Server mod differs from frozen run: '+relative)
            files[relative] = checksum
            entry = tarfile.TarInfo('mods/'+relative)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        if 'mod-list.json' not in files or not set(expected) <= set(files):
            raise ValueError('Incomplete server mod set')
        data = json.dumps({'files': files}).encode()
        entry = tarfile.TarInfo('sync-manifest.json')
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
    return files


def apply_archive(archive_path, gui_mods, backup_root):
    """Local seam also executed on Starship; never deletes a previous mod copy."""
    import shutil
    gui_mods, backup_root = Path(gui_mods), Path(backup_root)
    stage = backup_root/'incoming'
    stage.mkdir(parents=True, exist_ok=False)
    total = 0
    with tarfile.open(archive_path) as archive:
        for entry in archive:
            relative = Path(entry.name)
            if (not entry.isfile() or relative.is_absolute() or '..' in relative.parts
                    or not (entry.name == 'sync-manifest.json' or relative.parts[0] == 'mods')):
                raise ValueError('Unsafe mod archive entry')
            total += entry.size
            if total > 512*1024*1024:
                raise ValueError('Mod archive exceeds size limit')
            target = stage/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                shutil.copyfileobj(archive.extractfile(entry), stream)
    manifest = json.loads((stage/'sync-manifest.json').read_text())['files']
    actual = {p.relative_to(stage/'mods').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (stage/'mods').rglob('*') if p.is_file()}
    if actual != manifest:
        raise ValueError('Transferred mod hashes differ')
    json.loads((stage/'mods/mod-list.json').read_text())
    gui_mods.mkdir(parents=True, exist_ok=True)
    (backup_root/'previous').mkdir()
    replaced, installed = [], []
    try:
        for incoming in sorted((stage/'mods').iterdir()):
            target = gui_mods/incoming.name
            if target.exists() or target.is_symlink():
                shutil.move(str(target), backup_root/'previous'/incoming.name)
                replaced.append(incoming.name)
            shutil.move(str(incoming), target)
            installed.append(incoming.name)
    except Exception:
        (backup_root/'failed').mkdir(exist_ok=True)
        for name in installed:
            shutil.move(str(gui_mods/name), backup_root/'failed'/name)
        for name in replaced:
            shutil.move(str(backup_root/'previous'/name), gui_mods/name)
        raise
    return {'files_verified': len(actual), 'backup': str(backup_root/'previous'),
            'restart_client_required': True}


def sync_to_starship(mods, expected, data_root):
    import inspect
    identifier = 'sync-'+uuid.uuid4().hex
    local = Path(data_root)/'mod-sync'/identifier
    local.mkdir(parents=True)
    archive = local/'mods.tar.gz'
    make_archive(mods, expected, archive)
    # A fixed SSH host and fixed GUI path: user input never becomes shell code.
    remote = '''import hashlib,json,tarfile,shutil,sys
from pathlib import Path
''' + inspect.getsource(apply_archive) + '''
root=Path.home()/'.local/share/laya-cohort-mod-sync'/sys.argv[1]
root.mkdir(parents=True,exist_ok=False)
archive=root/'mods.tar.gz'
with archive.open('xb') as f: shutil.copyfileobj(sys.stdin.buffer,f)
print(json.dumps(apply_archive(archive,Path.home()/'.factorio/mods',root)))
'''
    with archive.open('rb') as stream:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            'starship', 'python3 -c '+shlex.quote(remote)+' '+shlex.quote(identifier)],
            stdin=stream, capture_output=True, timeout=180, check=True)
    receipt = json.loads(result.stdout)
    (local/'receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt

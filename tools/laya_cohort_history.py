# Path: tools/laya_cohort_history.py | Purpose: Incremental host history indexing and extrema-preserving chart windows.
from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
import threading
import time

FIELDS = ('updated', 'reason', 'mode', 'target_slots', 'active_slots', 'effective_max', 'manual_target')
METRICS = ('ups_p95', 'ups_p98', 'total_ups', 'temp_c', 'available_gb', 'ups_samples')


class History:
    def __init__(self, source, database):
        self.source, self.database = Path(source), Path(database)
        self.lock = threading.Lock()

    def window(self, hours, *, now=None):
        if hours not in (6, 24, 168):
            raise ValueError('History range must be 6, 24 or 168 hours')
        now = time.time() if now is None else now
        with self.lock, sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS samples (time REAL PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY, inode INTEGER, offset INTEGER)')
            previous = db.execute('SELECT inode,offset FROM cursor WHERE id=1').fetchone()
            try:
                stat = self.source.stat()
                offset = previous[1] if previous and previous[0] == stat.st_ino and previous[1] <= stat.st_size else 0
                with self.source.open('rb') as stream:
                    stream.seek(offset)
                    stop = offset+32*1024*1024
                    while stream.tell() < stop:
                        start = stream.tell()
                        line = stream.readline()
                        if not line or not line.endswith(b'\n'):
                            stream.seek(start)
                            break
                        try:
                            row = json.loads(line)
                            if type(row.get('updated')) not in (int, float) or not math.isfinite(row['updated']):
                                continue
                            compact = {key: row.get(key) for key in FIELDS}
                            compact['metrics'] = {key: (row.get('metrics') or {}).get(key) for key in METRICS}
                            db.execute('INSERT OR REPLACE INTO samples VALUES (?,?)',
                                (row['updated'], json.dumps(compact, allow_nan=False)))
                        except (ValueError, TypeError, AttributeError):
                            continue
                    offset = stream.tell()
                db.execute('INSERT OR REPLACE INTO cursor VALUES (1,?,?)', (stat.st_ino, offset))
                catching_up = offset < stat.st_size
            except FileNotFoundError:
                catching_up = False
            db.execute('DELETE FROM samples WHERE time < ?', (now-30*86400,))
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM samples WHERE time >= ? AND time <= ? ORDER BY time', (now-hours*3600, now))]
        durations = {}
        for first, second in zip(rows, rows[1:]):
            delta = second['updated']-first['updated']
            if 0 < delta <= 600:
                key = first.get('reason') or 'unknown'
                durations[key] = durations.get(key, 0)+delta
        gaps = [0]
        for first, second in zip(rows, rows[1:]):
            gaps.append(gaps[-1]+int(second['updated']-first['updated'] > 600))
        original = len(rows)
        if original > 1500:
            # Retain original measured extrema, rather than averaging quantiles
            # into a new quantity or hiding short thermal/UPS excursions.
            selected = {0, original-1}
            size = math.ceil(original/100)
            accessors = [lambda r, key=key: (r.get('metrics') or {}).get(key) for key in METRICS[:4]]
            accessors += [lambda r: r.get('target_slots'), lambda r: r.get('active_slots')]
            for start in range(0, original, size):
                indices = range(start, min(original, start+size))
                selected.add(start)
                for get in accessors:
                    measured = [i for i in indices if type(get(rows[i])) in (int, float) and math.isfinite(get(rows[i]))]
                    if measured:
                        selected.add(min(measured, key=lambda i: get(rows[i])))
                        selected.add(max(measured, key=lambda i: get(rows[i])))
            indices = sorted(selected)
            chosen = []
            previous = 0
            for index in indices:
                row = dict(rows[index])
                row['gap_before'] = gaps[index] > gaps[previous]
                chosen.append(row)
                previous = index
            rows = chosen
        else:
            for index, row in enumerate(rows):
                row['gap_before'] = index > 0 and gaps[index] > gaps[index-1]
        return {'rows': rows, 'hours': hours, 'raw_samples': original,
                'reason_seconds': durations, 'catching_up': catching_up,
                'display': 'original samples, extrema retained when downsampled',
                'start': now-hours*3600, 'end': now}

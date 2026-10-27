"""Compatibility probe: the unchanged event decoder from Squadra ce96f7d.

Only its surrounding imports, helpers and command-line driver are supplied here.
No current registry module is imported by this pre-refresh reader.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(body):
    return hashlib.sha256(body).hexdigest()


def decode_event(row):
    event = {k: row[k] for k in ["id", "utc", "kind", "actor", "previous"]}
    event["data"] = json.loads(row["data"])
    version = row['hash_format'] if 'hash_format' in row.keys() else None
    require(version in (None, 2, 3), "Unsupported event hash format")
    if version is not None:
        event['format'] = version
    require(digest(canonical(event).encode()) == row["hash"],
            "Event integrity failure at sequence " + str(row["seq"]))
    return {**event, "hash": row["hash"], "seq": row["seq"]}


if __name__ == '__main__':
    db = sqlite3.connect((Path(sys.argv[1]) / 'registry.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        for row in db.execute('SELECT * FROM events ORDER BY seq'):
            decode_event(row)
    finally:
        db.close()

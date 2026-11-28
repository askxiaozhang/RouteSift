"""SQLite persistence for five-minute aggregates, checkpoints and reviews."""

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

BUCKET_SECONDS = 300
SHADOWROCKET_TABLES = {"file": "shadowrocket_connections", "live": "shadowrocket_live_connections"}

TRAFFIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic_stats (
    bucket_ts    INTEGER NOT NULL,
    root_domain  TEXT NOT NULL,
    host         TEXT NOT NULL,
    process      TEXT NOT NULL,
    rule_type    TEXT NOT NULL,
    rule_payload TEXT NOT NULL DEFAULT '',
    proxy_group  TEXT NOT NULL,
    proxy_node   TEXT NOT NULL DEFAULT '',
    route_type   TEXT NOT NULL CHECK (route_type IN ('PROXY','DIRECT','REJECT','UNKNOWN')),
    is_proxy     INTEGER GENERATED ALWAYS AS (route_type = 'PROXY') VIRTUAL,
    up_bytes     INTEGER NOT NULL,
    down_bytes   INTEGER NOT NULL,
    conn_count   INTEGER NOT NULL,
    PRIMARY KEY (bucket_ts, host, process, rule_type, rule_payload, proxy_group, proxy_node, route_type)
) WITHOUT ROWID;
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS sample_buckets (
    bucket_ts INTEGER PRIMARY KEY,
    sample_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS active_connections (
    id TEXT PRIMARY KEY,
    start TEXT NOT NULL,
    up_bytes INTEGER NOT NULL,
    down_bytes INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS rule_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_type TEXT NOT NULL CHECK (match_type IN ('DOMAIN','DOMAIN-SUFFIX','PROCESS-NAME','IP-CIDR')),
    match_value TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('DIRECT','KEEP_PROXY','REJECT','IGNORE','MATCH_TARGET')),
    remark TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE (match_type, match_value)
);

CREATE TABLE IF NOT EXISTS shadowrocket_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    rules TEXT NOT NULL,
    version INTEGER NOT NULL
);
INSERT OR IGNORE INTO shadowrocket_config (id, rules, version) VALUES (1, '', 0);

CREATE TABLE IF NOT EXISTS shadowrocket_connections (
    observed_at INTEGER NOT NULL,
    log_id INTEGER NOT NULL,
    host TEXT NOT NULL,
    route_type TEXT NOT NULL CHECK (route_type IN ('PROXY','DIRECT','REJECT','UNKNOWN')),
    PRIMARY KEY (observed_at, log_id, host, route_type)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS shadowrocket_host_time ON shadowrocket_connections(host, observed_at);

CREATE TABLE IF NOT EXISTS shadowrocket_live_connections (
    observed_at INTEGER NOT NULL,
    log_id INTEGER NOT NULL,
    host TEXT NOT NULL,
    route_type TEXT NOT NULL CHECK (route_type IN ('PROXY','DIRECT','REJECT','UNKNOWN')),
    PRIMARY KEY (observed_at, log_id, host, route_type)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS shadowrocket_live_host_time ON shadowrocket_live_connections(host, observed_at);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def exclusive_collector(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(f"{self.path}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("该数据库已有一个采集实例在运行") from exc
            yield
        finally:
            os.close(fd)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def initialize(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            decision_schema = db.execute("SELECT sql FROM sqlite_master WHERE name = 'rule_decisions'").fetchone()[0]
            if "MATCH_TARGET" not in decision_schema:
                db.execute("ALTER TABLE rule_decisions RENAME TO rule_decisions_v1")
                db.execute(decision_schema.replace("'IGNORE'", "'IGNORE','MATCH_TARGET'").replace("IF NOT EXISTS ", ""))
                db.execute("INSERT INTO rule_decisions SELECT * FROM rule_decisions_v1")
                db.execute("DROP TABLE rule_decisions_v1")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(traffic_stats)")}
            if columns and "rule_payload" not in columns:
                db.execute("ALTER TABLE traffic_stats RENAME TO traffic_stats_v1")
                db.execute(TRAFFIC_SCHEMA)
                db.execute(
                    """
                    INSERT INTO traffic_stats (
                        bucket_ts, root_domain, host, process, rule_type, proxy_group,
                        route_type, up_bytes, down_bytes, conn_count
                    )
                    SELECT bucket_ts, root_domain, host, process, rule_type, proxy_group,
                           route_type, up_bytes, down_bytes, conn_count
                    FROM traffic_stats_v1
                    """
                )
                db.execute("DROP TABLE traffic_stats_v1")
            else:
                db.execute(TRAFFIC_SCHEMA)
            db.execute("CREATE INDEX IF NOT EXISTS traffic_host_time ON traffic_stats(host, bucket_ts)")

    def checkpoints(self):
        with self.connect() as db:
            return {
                row["id"]: (row["start"], row["up_bytes"], row["down_bytes"])
                for row in db.execute("SELECT * FROM active_connections")
            }

    def flush(self, aggregates, samples, active):
        with self.connect() as db:
            db.executemany(
                """
                INSERT INTO traffic_stats (
                    bucket_ts, root_domain, host, process, rule_type, rule_payload,
                    proxy_group, proxy_node, route_type, up_bytes, down_bytes, conn_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET
                    up_bytes = up_bytes + excluded.up_bytes,
                    down_bytes = down_bytes + excluded.down_bytes,
                    conn_count = conn_count + excluded.conn_count
                """,
                ((*key, *counts) for key, counts in aggregates.items()),
            )
            db.executemany(
                """
                INSERT INTO sample_buckets VALUES (?, ?)
                ON CONFLICT DO UPDATE SET sample_count = sample_count + excluded.sample_count
                """,
                samples.items(),
            )
            db.execute("DELETE FROM active_connections")
            db.executemany(
                "INSERT INTO active_connections VALUES (?, ?, ?, ?)",
                ((conn_id, *checkpoint) for conn_id, checkpoint in active.items()),
            )

    def decisions(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM rule_decisions ORDER BY updated_at DESC, id DESC")
            ]

    def save_decision(self, match_type, match_value, action, remark):
        with self.connect() as db:
            row = db.execute(
                """
                INSERT INTO rule_decisions (match_type, match_value, action, remark)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (match_type, match_value) DO UPDATE SET
                    action = excluded.action, remark = excluded.remark, updated_at = unixepoch()
                RETURNING *
                """,
                (match_type, match_value, action, remark),
            ).fetchone()
            return dict(row)

    def remove_decision(self, decision_id):
        with self.connect() as db:
            return db.execute("DELETE FROM rule_decisions WHERE id = ?", (decision_id,)).rowcount == 1

    def shadowrocket_config(self):
        with self.connect() as db:
            return dict(db.execute("SELECT rules, version FROM shadowrocket_config WHERE id = 1").fetchone())

    def save_shadowrocket_config(self, rules, version):
        with self.connect() as db:
            row = db.execute(
                """
                UPDATE shadowrocket_config SET rules = ?, version = version + 1
                WHERE id = 1 AND version = ?
                RETURNING rules, version
                """,
                (rules, version),
            ).fetchone()
            return dict(row) if row else None

    def save_shadowrocket_connections(self, events, source="file"):
        table = SHADOWROCKET_TABLES[source]
        with self.connect() as db:
            db.executemany(
                f"INSERT INTO {table} VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                events,
            )
            return db.total_changes

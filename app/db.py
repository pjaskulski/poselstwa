from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from flask import Flask

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'instance' / 'poselstwa.sqlite'
SCHEMA_PATH = BASE_DIR / 'doc' / 'schemat_bazy.sql'
SCHEMA_VERSION = 1
REQUIRED_TABLES = (
    'app_user',
    'historical_date',
    'bibliography_item',
    'person',
    'embassy',
    'person_name_variant',
    'biography_note',
    'reference_mention',
    'office_term',
    'curia_presence',
    'embassy_participant',
    'embassy_bibliography',
    'source_text',
    'source_segment',
    'theme',
    'theme_annotation',
    'source_type_dictionary',
    'office_type_dictionary',
)


def get_db(app: Flask) -> sqlite3.Connection:
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    ensure_base_schema(db)
    migrate_schema(db)
    return db


def ensure_base_schema(db: sqlite3.Connection) -> None:
    existing_tables = {
        row['name']
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if all(table in existing_tables for table in REQUIRED_TABLES):
        return
    if not SCHEMA_PATH.exists():
        raise RuntimeError(f'Brakuje pliku schematu bazy: {SCHEMA_PATH}')
    db.executescript(SCHEMA_PATH.read_text(encoding='utf-8'))
    db.commit()


def migrate_schema(db: sqlite3.Connection) -> None:
    ensure_schema_version_table(db)
    version = get_schema_version(db)
    while version < SCHEMA_VERSION:
        next_version = version + 1
        apply_schema_migration(db, next_version)
        set_schema_version(db, next_version)
        version = next_version


def ensure_schema_version_table(db: sqlite3.Connection) -> None:
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS app_schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    db.commit()


def get_schema_version(db: sqlite3.Connection) -> int:
    ensure_schema_version_table(db)
    row = db.execute('SELECT version FROM app_schema_version WHERE id = 1').fetchone()
    return int(row['version']) if row else 0


def set_schema_version(db: sqlite3.Connection, version: int) -> None:
    db.execute(
        '''
        INSERT INTO app_schema_version (id, version, updated_at)
        VALUES (1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE
        SET version = excluded.version,
            updated_at = excluded.updated_at
        ''',
        (version,),
    )
    db.commit()


def apply_schema_migration(db: sqlite3.Connection, target_version: int) -> None:
    if target_version == 1:
        migrate_schema_to_v1(db)
        return
    raise RuntimeError(f'Nieobsługiwana migracja schematu: {target_version}')


def migrate_schema_to_v1(db: sqlite3.Connection) -> None:
    ensure_parameter_tables(db)
    ensure_historical_date_compatibility(db)
    ensure_bibliography_item_compatibility(db)
    ensure_embassy_bibliography_compatibility(db)
    ensure_biography_note_compatibility(db)
    ensure_office_term_compatibility(db)
    ensure_source_text_compatibility(db)
    ensure_curia_presence_compatibility(db)


def ensure_parameter_tables(db: sqlite3.Connection) -> None:
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS source_type_dictionary (
            id INTEGER PRIMARY KEY,
            uuid TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS office_type_dictionary (
            id INTEGER PRIMARY KEY,
            uuid TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    existing_values = {row['value'] for row in db.execute('SELECT value FROM source_type_dictionary').fetchall()}
    source_values = [
        row['source_type']
        for row in db.execute(
            '''
            SELECT DISTINCT source_type
            FROM source_text
            WHERE source_type IS NOT NULL AND TRIM(source_type) <> ''
            ORDER BY source_type ASC
            '''
        ).fetchall()
    ]
    for index, value in enumerate(source_values, start=1):
        if value in existing_values:
            continue
        db.execute(
            '''
            INSERT INTO source_type_dictionary (uuid, value, label, sort_order, is_active)
            VALUES (?, ?, ?, ?, 1)
            ''',
            (str(uuid.uuid4()), value, value, index * 10),
        )
    existing_office_values = {row['value'] for row in db.execute('SELECT value FROM office_type_dictionary').fetchall()}
    office_values = [
        'urząd kościelny',
        'urząd świecki',
        'inny',
    ]
    for index, value in enumerate(office_values, start=1):
        if value in existing_office_values:
            continue
        db.execute(
            '''
            INSERT INTO office_type_dictionary (uuid, value, label, sort_order, is_active)
            VALUES (?, ?, ?, ?, 1)
            ''',
            (str(uuid.uuid4()), value, value, index * 10),
        )
    db.commit()


def ensure_historical_date_compatibility(db: sqlite3.Connection) -> None:
    db.execute(
        '''
        UPDATE historical_date
        SET date_kind = CASE
            WHEN date_kind IN ('month', 'year') THEN 'exact'
            WHEN date_kind = 'range' THEN 'circa'
            ELSE date_kind
        END
        WHERE date_kind IN ('month', 'year', 'range')
        '''
    )
    db.execute(
        '''
        UPDATE historical_date
        SET certainty = 'uncertain'
        WHERE certainty = 'possible'
        '''
    )
    db.commit()


def ensure_bibliography_item_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('bibliography_item')").fetchall()
    }
    changed = False
    if 'publisher_text' not in columns:
        db.execute("ALTER TABLE bibliography_item ADD COLUMN publisher_text TEXT")
        changed = True
    if 'journal_title' not in columns:
        db.execute("ALTER TABLE bibliography_item ADD COLUMN journal_title TEXT")
        changed = True
    if 'book_title' not in columns:
        db.execute("ALTER TABLE bibliography_item ADD COLUMN book_title TEXT")
        changed = True
    if changed:
        db.commit()


def ensure_embassy_bibliography_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('embassy_bibliography')").fetchall()
    }
    if 'page_range' not in columns:
        db.execute("ALTER TABLE embassy_bibliography ADD COLUMN page_range TEXT")
        db.commit()


def ensure_biography_note_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('biography_note')").fetchall()
    }
    if 'reference_locator' not in columns:
        db.execute("ALTER TABLE biography_note ADD COLUMN reference_locator TEXT")
        db.commit()


def ensure_office_term_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('office_term')").fetchall()
    }
    if 'reference_locator' not in columns:
        db.execute("ALTER TABLE office_term ADD COLUMN reference_locator TEXT")
        db.commit()


def ensure_source_text_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('source_text')").fetchall()
    }
    if 'reference_locator' not in columns:
        db.execute("ALTER TABLE source_text ADD COLUMN reference_locator TEXT")
        db.commit()


def ensure_curia_presence_compatibility(db: sqlite3.Connection) -> None:
    columns = {
        row['name']
        for row in db.execute("PRAGMA table_info('curia_presence')").fetchall()
    }
    changed = False
    if 'bibliography_item_id' not in columns:
        db.execute("ALTER TABLE curia_presence ADD COLUMN bibliography_item_id INTEGER")
        changed = True
    if 'reference_locator' not in columns:
        db.execute("ALTER TABLE curia_presence ADD COLUMN reference_locator TEXT")
        changed = True
    if 'papal_register_text' not in columns:
        db.execute("ALTER TABLE curia_presence ADD COLUMN papal_register_text TEXT")
        changed = True
    if 'note_text' not in columns:
        db.execute("ALTER TABLE curia_presence ADD COLUMN note_text TEXT")
        changed = True
    if changed:
        db.commit()

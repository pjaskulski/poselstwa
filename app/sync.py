from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.db import REQUIRED_TABLES, get_schema_version, migrate_schema

BACKUP_SUFFIX_IMPORT = '-before-import.sqlite'
BACKUP_SUFFIX_RESTORE = '-before-restore.sqlite'
BACKUP_SUFFIXES = (BACKUP_SUFFIX_IMPORT, BACKUP_SUFFIX_RESTORE)
SQLITE_HEADER = b'SQLite format 3\x00'
IMPORT_REQUIRED_TABLES = tuple(
    table_name
    for table_name in REQUIRED_TABLES
    if table_name not in {'source_type_dictionary', 'office_type_dictionary'}
)


class DatabaseSyncError(RuntimeError):
    pass


@dataclass(slots=True)
class DatabaseMetadata:
    schema_version: int
    person_count: int
    embassy_count: int


@dataclass(slots=True)
class ImportResult:
    backup_path: Path
    metadata: DatabaseMetadata


@dataclass(slots=True)
class BackupEntry:
    filename: str
    modified_at: str
    metadata: DatabaseMetadata | None
    is_valid: bool
    error: str | None = None


def backups_dir_for(db_path: Path) -> Path:
    return db_path.parent / 'backups'


def temp_dir_for(db_path: Path) -> Path:
    return db_path.parent / 'tmp'


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_metadata(db_path: Path) -> DatabaseMetadata:
    if not db_path.exists():
        raise DatabaseSyncError(f'Nie znaleziono pliku bazy: {db_path}')
    try:
        conn = open_readonly_connection(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise DatabaseSyncError(f'Nie udało się otworzyć bazy: {exc}') from exc
    try:
        schema_version = schema_version_from_connection(conn)
        person_count = conn.execute('SELECT COUNT(*) FROM person').fetchone()[0]
        embassy_count = conn.execute('SELECT COUNT(*) FROM embassy').fetchone()[0]
    except sqlite3.Error as exc:
        raise DatabaseSyncError(f'Nie udało się odczytać metadanych bazy: {exc}') from exc
    finally:
        conn.close()
    return DatabaseMetadata(
        schema_version=int(schema_version),
        person_count=int(person_count),
        embassy_count=int(embassy_count),
    )


def export_filename(prefix: str = 'poselstwa') -> str:
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    return f'{prefix}-{timestamp}.sqlite'


def create_database_backup(db_path: Path, suffix: str = BACKUP_SUFFIX_IMPORT) -> Path:
    if not db_path.exists():
        raise DatabaseSyncError('Nie znaleziono bieżącej bazy do zarchiwizowania.')
    backup_dir = ensure_directory(backups_dir_for(db_path))
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_path = backup_dir / f'poselstwa-{timestamp}{suffix}'
    try:
        shutil.copy2(db_path, backup_path)
    except OSError as exc:
        raise DatabaseSyncError(f'Nie udało się utworzyć kopii bezpieczeństwa: {exc}') from exc
    return backup_path


def save_upload_to_temporary_file(upload_storage, db_path: Path) -> Path:
    temp_dir = ensure_directory(temp_dir_for(db_path))
    fd, temp_name = tempfile.mkstemp(prefix='import-', suffix='.sqlite', dir=temp_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        upload_storage.save(temp_path)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise DatabaseSyncError(f'Nie udało się zapisać przesłanego pliku: {exc}') from exc
    return temp_path


def import_database_file(
    import_path: Path,
    db_path: Path,
    *,
    backup_suffix: str = BACKUP_SUFFIX_IMPORT,
) -> ImportResult:
    metadata = validate_import_database(import_path)
    backup_path = create_database_backup(db_path, backup_suffix)
    try:
        os.replace(import_path, db_path)
    except OSError as exc:
        raise DatabaseSyncError(f'Nie udało się podmienić bazy danych: {exc}') from exc
    return ImportResult(backup_path=backup_path, metadata=metadata)


def validate_import_database(import_path: Path) -> DatabaseMetadata:
    if not import_path.exists():
        raise DatabaseSyncError('Przesłany plik bazy nie został zapisany.')
    if not looks_like_sqlite_file(import_path):
        raise DatabaseSyncError('Przesłany plik nie wygląda na bazę SQLite.')
    try:
        conn = sqlite3.connect(import_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
    except sqlite3.Error as exc:
        raise DatabaseSyncError(f'Nie udało się otworzyć przesłanej bazy: {exc}') from exc
    try:
        quick_check(conn)
        require_tables(conn, IMPORT_REQUIRED_TABLES)
        migrate_schema(conn)
        require_tables(conn, REQUIRED_TABLES)
        quick_check(conn)
        foreign_key_check(conn)
        return DatabaseMetadata(
            schema_version=int(get_schema_version(conn)),
            person_count=int(conn.execute('SELECT COUNT(*) FROM person').fetchone()[0]),
            embassy_count=int(conn.execute('SELECT COUNT(*) FROM embassy').fetchone()[0]),
        )
    except sqlite3.Error as exc:
        raise DatabaseSyncError(f'Błąd walidacji przesłanej bazy: {exc}') from exc
    finally:
        conn.close()


def looks_like_sqlite_file(path: Path) -> bool:
    if path.stat().st_size < len(SQLITE_HEADER):
        return False
    with path.open('rb') as handle:
        header = handle.read(len(SQLITE_HEADER))
    return header == SQLITE_HEADER


def quick_check(conn: sqlite3.Connection) -> None:
    row = conn.execute('PRAGMA quick_check').fetchone()
    if not row or str(row[0]).lower() != 'ok':
        raise DatabaseSyncError('Przesłana baza nie przechodzi kontroli integralności SQLite.')


def foreign_key_check(conn: sqlite3.Connection) -> None:
    row = conn.execute('PRAGMA foreign_key_check').fetchone()
    if row is not None:
        raise DatabaseSyncError('Przesłana baza zawiera niespójne klucze obce.')


def require_tables(conn: sqlite3.Connection, required_tables: tuple[str, ...]) -> None:
    existing_tables = {
        row['name']
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    missing = [table_name for table_name in required_tables if table_name not in existing_tables]
    if missing:
        missing_label = ', '.join(sorted(missing))
        raise DatabaseSyncError(f'Brakuje wymaganych tabel: {missing_label}.')


def list_backups(db_path: Path) -> list[BackupEntry]:
    backup_dir = backups_dir_for(db_path)
    if not backup_dir.exists():
        return []
    entries: list[BackupEntry] = []
    for backup_path in sorted(
        (
            path
            for path in backup_dir.iterdir()
            if path.is_file() and any(path.name.endswith(suffix) for suffix in BACKUP_SUFFIXES)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        modified_at = datetime.fromtimestamp(backup_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        try:
            metadata = database_metadata(backup_path)
            entries.append(
                BackupEntry(
                    filename=backup_path.name,
                    modified_at=modified_at,
                    metadata=metadata,
                    is_valid=True,
                )
            )
        except DatabaseSyncError as exc:
            entries.append(
                BackupEntry(
                    filename=backup_path.name,
                    modified_at=modified_at,
                    metadata=None,
                    is_valid=False,
                    error=str(exc),
                )
            )
    return entries


def restore_database_backup(filename: str, db_path: Path) -> ImportResult:
    backup_path = backup_path_by_name(db_path, filename)
    temp_path = copy_backup_to_temporary_file(backup_path, db_path)
    try:
        return import_database_file(temp_path, db_path, backup_suffix=BACKUP_SUFFIX_RESTORE)
    finally:
        temp_path.unlink(missing_ok=True)


def backup_path_by_name(db_path: Path, filename: str) -> Path:
    normalized_name = Path(filename).name
    if normalized_name != filename:
        raise DatabaseSyncError('Nieprawidłowa nazwa pliku backupu.')
    if not any(normalized_name.endswith(suffix) for suffix in BACKUP_SUFFIXES):
        raise DatabaseSyncError('Wybrany plik nie jest obsługiwanym backupem bazy.')
    backup_path = backups_dir_for(db_path) / normalized_name
    if not backup_path.exists() or not backup_path.is_file():
        raise DatabaseSyncError('Nie znaleziono wskazanego backupu bazy.')
    return backup_path


def copy_backup_to_temporary_file(backup_path: Path, db_path: Path) -> Path:
    temp_dir = ensure_directory(temp_dir_for(db_path))
    fd, temp_name = tempfile.mkstemp(prefix='restore-', suffix='.sqlite', dir=temp_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        shutil.copy2(backup_path, temp_path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise DatabaseSyncError(f'Nie udało się przygotować backupu do przywrócenia: {exc}') from exc
    return temp_path


def open_readonly_connection(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)


def schema_version_from_connection(conn: sqlite3.Connection) -> int:
    existing_tables = {
        row['name']
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if 'app_schema_version' not in existing_tables:
        return 0
    row = conn.execute('SELECT version FROM app_schema_version WHERE id = 1').fetchone()
    return int(row[0]) if row else 0

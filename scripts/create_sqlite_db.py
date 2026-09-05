from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = API_DIR / "storage" / "app.db"
SCHEMA_PATH = API_DIR / "database" / "schema.sql"


def create_database(database_path: Path = DEFAULT_DATABASE_PATH) -> Path:
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    from database.repository import _ensure_consent_columns

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        _ensure_consent_columns(connection)
        connection.commit()

    return database_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cria a base SQLite da API Analista de Vagas.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"Caminho do arquivo SQLite. Padrao: {DEFAULT_DATABASE_PATH}",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_path = create_database(args.database)
    print(f"Base SQLite criada/atualizada em: {database_path}")


if __name__ == "__main__":
    main()

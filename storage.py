"""Application cache storage with PostgreSQL and local JSON backends."""

import json
import os


CACHE_FILE = "applications.json"
TABLE_NAME = "job_tracker_cache"


def database_url():
    return os.getenv("DATABASE_URL", "").strip()


def load_json_cache(path=CACHE_FILE):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as cache_file:
        return json.load(cache_file)


def save_json_cache(cache, path=CACHE_FILE):
    with open(path, "w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, indent=2, ensure_ascii=False)


def _connect():
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured.")

    import psycopg

    return psycopg.connect(url)


def _ensure_table(connection):
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            email_id TEXT PRIMARY KEY,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def load_postgres_cache():
    with _connect() as connection:
        _ensure_table(connection)
        rows = connection.execute(
            f"SELECT email_id, payload FROM {TABLE_NAME}"
        ).fetchall()

        # On first use in Streamlit Cloud, preserve its existing runtime cache
        # if it is still present. This must happen before switching the app to
        # PostgreSQL, and only when the database is empty.
        if not rows:
            legacy_cache = load_json_cache()
            if legacy_cache:
                from psycopg.types.json import Jsonb

                connection.executemany(
                    f"""
                    INSERT INTO {TABLE_NAME} (email_id, payload)
                    VALUES (%s, %s)
                    ON CONFLICT (email_id) DO NOTHING
                    """,
                    [(email_id, Jsonb(payload)) for email_id, payload in legacy_cache.items()],
                )
                rows = connection.execute(
                    f"SELECT email_id, payload FROM {TABLE_NAME}"
                ).fetchall()
    return {email_id: payload for email_id, payload in rows}


def save_postgres_cache(cache):
    from psycopg.types.json import Jsonb

    with _connect() as connection:
        _ensure_table(connection)
        if cache:
            connection.executemany(
                f"""
                INSERT INTO {TABLE_NAME} (email_id, payload)
                VALUES (%s, %s)
                ON CONFLICT (email_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """,
                [(email_id, Jsonb(payload)) for email_id, payload in cache.items()],
            )


def load_cache():
    if database_url():
        return load_postgres_cache()
    return load_json_cache()


def save_cache(cache):
    if database_url():
        save_postgres_cache(cache)
    else:
        save_json_cache(cache)

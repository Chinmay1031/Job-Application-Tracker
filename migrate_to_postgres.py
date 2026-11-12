"""Import the local applications.json cache into the configured PostgreSQL DB."""

import os

from dotenv import load_dotenv

from storage import load_json_cache, save_postgres_cache


def main():
    load_dotenv()
    if not os.getenv("DATABASE_URL", "").strip():
        raise SystemExit("Set DATABASE_URL in .env before running this migration.")

    cache = load_json_cache()
    if not cache:
        raise SystemExit("applications.json is missing or empty; nothing to migrate.")

    save_postgres_cache(cache)
    print(f"Migrated {len(cache)} cached email records to PostgreSQL.")


if __name__ == "__main__":
    main()

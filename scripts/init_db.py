#!/usr/bin/env python3
"""
Initialize the MIKE-1 database schema.

Usage:
    python scripts/init_db.py

Requires DATABASE_URL environment variable or pass as argument.
"""

import sys
import os
from pathlib import Path

# Add engine to path
sys.path.insert(0, str(Path(__file__).parent.parent / "engine" / "src"))

from dotenv import load_dotenv

load_dotenv()


def main():
    database_url = os.environ.get("DATABASE_URL")

    if len(sys.argv) > 1:
        database_url = sys.argv[1]

    if not database_url:
        print("ERROR: DATABASE_URL not set")
        print("Set it in .env or pass as argument:")
        print("  python scripts/init_db.py 'postgresql://...'")
        return 1

    print("Initializing MIKE-1 database...")
    print(f"URL: {database_url[:50]}...")

    try:
        import psycopg2

        # Read schema
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        # Connect and execute
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        print("Connected to database")
        print("Executing schema...")

        cur.execute(schema_sql)
        conn.commit()

        print("Schema created successfully!")

        # Verify tables
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)

        tables = cur.fetchall()
        print(f"\nCreated {len(tables)} tables:")
        for table in tables:
            print(f"  - {table[0]}")

        cur.close()
        conn.close()

        print("\nDatabase initialization complete!")
        return 0

    except Exception as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

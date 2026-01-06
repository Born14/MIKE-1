"""Initialize MIKE-1 database schema in NeonDB."""

import os
import sys

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

def init_database():
    """Create all tables in NeonDB."""
    import psycopg2

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("ERROR: Missing DATABASE_URL in .env")
        return False

    print(f"Connecting to NeonDB...")
    print(f"  Host: {database_url.split('@')[1].split('/')[0] if '@' in database_url else 'unknown'}")

    # Read schema file
    schema_path = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")

    if not os.path.exists(schema_path):
        print(f"ERROR: Schema file not found at {schema_path}")
        return False

    with open(schema_path, "r") as f:
        schema_sql = f.read()

    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        cursor = conn.cursor()

        print("Executing schema...")
        cursor.execute(schema_sql)

        print("SUCCESS: Database schema created!")

        # Verify tables exist
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)

        tables = cursor.fetchall()
        print(f"\nTables created ({len(tables)}):")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            print(f"  - {table[0]}: {count} rows")

        # Verify views exist
        cursor.execute("""
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)

        views = cursor.fetchall()
        print(f"\nViews created ({len(views)}):")
        for view in views:
            print(f"  - {view[0]}")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)

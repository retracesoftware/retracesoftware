import os
import psycopg2

# Allow configuring DB via env so it works in Docker networks
DATABASE_CONFIG = {
    "dbname": os.getenv("DB_NAME", "test_db"),
    "user": os.getenv("DB_USER", "test_user"),
    "password": os.getenv("DB_PASSWORD", "test_password"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
}

def create_connection():
    """Establish a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(**DATABASE_CONFIG)
        host = f"{DATABASE_CONFIG['host']}:{DATABASE_CONFIG['port']}/{DATABASE_CONFIG['dbname']}"
        print(f"✓ Database connection successful to {host} as {DATABASE_CONFIG['user']}")
        return conn
    except Exception as e:
        print(f"✗ Error connecting to the database: {e}")
        raise

def test_psycopg2_connection():
    """Test if a connection to the PostgreSQL database can be established."""
    conn = create_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1;")
        assert cursor.fetchone()[0] == 1
        print("✓ Basic query succeeded")
    finally:
        cursor.close()
        conn.close()

def test_create_table():
    """Test if a table can be created and data can be inserted."""
    conn = create_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DROP TABLE IF EXISTS test_table;")
        conn.commit()

        cursor.execute("""
            CREATE TABLE test_table (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100),
                age INT
            );
        """)
        conn.commit()
        print("✓ Table created successfully!")

        insert_query = "INSERT INTO test_table (name, age) VALUES (%s, %s);"
        cursor.execute(insert_query, ("John Doe", 30))
        cursor.execute(insert_query, ("Jane Smith", 25))
        conn.commit()
        print("✓ Data inserted successfully!")

        cursor.execute("SELECT name, age FROM test_table ORDER BY id;")
        rows = cursor.fetchall()
        print("✓ Query Result:", rows)

        assert len(rows) == 2, "Two rows should be inserted into the table."
        assert rows[0][0] == "John Doe", "The first row's name should be 'John Doe'."
        assert rows[1][0] == "Jane Smith", "The second row's name should be 'Jane Smith'."

        cursor.execute("DROP TABLE test_table;")
        conn.commit()
        print("✓ Cleaned up test table")

    finally:
        cursor.close()
        conn.close()

def test():
    print("\n" + "=" * 60)
    print("psycopg2 Test Suite")
    print("=" * 60 + "\n")

    test_psycopg2_connection()
    print()
    test_create_table()

    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)

if __name__ == "__main__":
    test()

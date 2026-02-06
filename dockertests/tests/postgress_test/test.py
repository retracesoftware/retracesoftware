import os
import psycopg2

def create_connection():
    """Establish a connection to the PostgreSQL database using DATABASE_URL env var."""
    database_url = os.environ.get('DATABASE_URL', 'postgres://postgres:test@postgres:5432/postgres')
    
    try:
        conn = psycopg2.connect(database_url)
        print(f"✓ Database connection successful to {database_url.split('@')[1]}")
        return conn
    except Exception as e:
        print(f"✗ Error connecting to the database: {e}")
        raise


def test_psycopg2_connection():
    """Test basic psycopg2 connection."""
    conn = create_connection()
    cursor = conn.cursor()
    
    try:
        # Execute a simple query
        cursor.execute("SELECT version();")
        version = cursor.fetchone()
        print(f"✓ PostgreSQL version: {version[0][:50]}...")
        
        # Test basic operation
        cursor.execute("SELECT 1 + 1 AS result;")
        result = cursor.fetchone()
        assert result[0] == 2, f"Expected 2, got {result[0]}"
        print(f"✓ Query result: {result[0]}")
        
    finally:
        cursor.close()
        conn.close()


def test_create_table():
    """Test table creation and data insertion."""
    conn = create_connection()
    cursor = conn.cursor()
    
    try:
        # Create a test table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100),
                email VARCHAR(100)
            );
        """)
        print("✓ Created table 'test_users'")
        
        # Insert test data
        cursor.execute("""
            INSERT INTO test_users (name, email) 
            VALUES (%s, %s) 
            RETURNING id;
        """, ("John Doe", "john@example.com"))
        user_id = cursor.fetchone()[0]
        conn.commit()
        print(f"✓ Inserted user with ID: {user_id}")
        
        # Query the data
        cursor.execute("SELECT name, email FROM test_users WHERE id = %s;", (user_id,))
        user = cursor.fetchone()
        assert user == ("John Doe", "john@example.com"), f"Unexpected user data: {user}"
        print(f"✓ Retrieved user: {user[0]} - {user[1]}")
        
        # Cleanup
        cursor.execute("DROP TABLE test_users;")
        conn.commit()
        print("✓ Cleaned up test table")
        
    finally:
        cursor.close()
        conn.close()


def test():
    """Main test entry point."""
    print("\n" + "="*60)
    print("PostgreSQL psycopg2 Test Suite")
    print("="*60 + "\n")
    
    test_psycopg2_connection()
    print()
    test_create_table()
    
    print("\n" + "="*60)
    print("✓ All tests passed!")
    print("="*60)


if __name__ == "__main__":
    test()

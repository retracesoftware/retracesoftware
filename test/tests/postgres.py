import psycopg2
import subprocess
import time

# Test metadata (used by run_all.py)
TAGS = ["slow", "database", "docker"]
TIMEOUT = 120

# Database configuration
DATABASE_CONFIG = {
    'dbname': 'test_db',
    'user': 'test_user',
    'password': 'test_password',
    'host': 'localhost',
    'port': 5433  # Use 5433 to avoid conflict with local PostgreSQL
}

CONTAINER_NAME = "test-postgres"


def setup():
    """Start PostgreSQL container before recording."""
    # Remove any existing container
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True
    )
    
    # Start new container
    result = subprocess.run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-e", "POSTGRES_USER=test_user",
        "-e", "POSTGRES_PASSWORD=test_password",
        "-e", "POSTGRES_DB=test_db",
        "-p", f"{DATABASE_CONFIG['port']}:5432",
        "postgres:15"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        raise RuntimeError(f"Failed to start PostgreSQL container: {result.stderr}")
    
    print(f"Started container: {CONTAINER_NAME}")
    
    # Wait for PostgreSQL to be ready
    print("Waiting for PostgreSQL to be ready...")
    for i in range(30):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "psql", 
             "-U", "test_user", "-d", "test_db", "-c", "SELECT 1"],
            capture_output=True
        )
        if result.returncode == 0:
            print("PostgreSQL is ready!")
            return
        time.sleep(1)
        print(f"  Waiting... ({i+1}/30)")
    
    raise RuntimeError("PostgreSQL failed to become ready in 30 seconds")


def teardown():
    """Stop PostgreSQL container after recording."""
    result = subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"Removed container: {CONTAINER_NAME}")
    else:
        print(f"Warning: Could not remove container: {result.stderr}")

def create_connection():
    """Establish a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(**DATABASE_CONFIG)
        print("Database connection successful!")
        return conn
    except Exception as e:
        print(f"Error connecting to the database: {e}")
        raise

def test_psycopg2_connection():
    """Test if a connection to the PostgreSQL database can be established."""
    conn = create_connection()
    assert conn is not None, "Connection to the PostgreSQL database should be established."
    conn.close()

def test_create_table():
    """Test if a table can be created and data can be inserted."""
    conn = create_connection()
    cursor = conn.cursor()

    try:
        # Drop the table if it already exists
        cursor.execute("DROP TABLE IF EXISTS test_table;")
        conn.commit()

        # Create a new table
        create_table_query = """
        CREATE TABLE test_table (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            age INT
        );
        """
        cursor.execute(create_table_query)
        conn.commit()
        print("Table created successfully!")

        # Insert some data
        insert_query = "INSERT INTO test_table (name, age) VALUES (%s, %s);"
        cursor.execute(insert_query, ('John Doe', 30))
        cursor.execute(insert_query, ('Jane Smith', 25))
        conn.commit()
        print("Data inserted successfully!")

        # Query the data to ensure it was inserted correctly
        cursor.execute("SELECT * FROM test_table;")
        rows = cursor.fetchall()
        print("Query Result:", rows)

        # Ensure the data is correct
        assert len(rows) == 2, "Two rows should be inserted into the table."
        assert rows[0][1] == 'John Doe', "The first row's name should be 'John Doe'."
        assert rows[1][1] == 'Jane Smith', "The second row's name should be 'Jane Smith'."
    finally:
        # Clean up by closing the cursor and connection
        cursor.close()
        conn.close()


def test():
    """Main test entry point - just the test logic, no setup/teardown."""
    test_psycopg2_connection()
    test_create_table()
    print("psycopg2 tests passed!")


if __name__ == "__main__":
    setup()
    try:
        test()
    finally:
        teardown()

from rich.console import Console
from rich.table import Table
from rich.progress import track
import time

# Create a Console instance
console = Console()

def test_rich_text():
    console.print("Hello, [bold magenta]Rich[/bold magenta]!", style="bold green")
    console.print("This is a test of the [underline]rich[/underline] library.", style="italic blue")

def test_rich_table():
    table = Table(title="Sample Table")

    # Add columns
    table.add_column("Name", style="cyan", justify="left")
    table.add_column("Age", style="magenta", justify="right")
    table.add_column("Occupation", style="green", justify="left")

    # Add rows
    table.add_row("Alice", "24", "Engineer")
    table.add_row("Bob", "30", "Artist")
    table.add_row("Charlie", "29", "Doctor")

    console.print(table)

def test_rich_progress():
    for task in track(range(10), description="Processing..."):
        time.sleep(0.1)  # Simulate work

if __name__ == "__main__":
    print("Testing styled text:")
    test_rich_text()
    
    print("\nTesting table display:")
    test_rich_table()
    
    print("\nTesting progress bar:")
    test_rich_progress()

    console.print("Test complete!", style="bold underline green") 
"""
Simple test demonstrating automatic docker-compose.yml generation.

This test doesn't have a docker-compose.yml file - the runner
automatically uses a default configuration.
"""

def test():
    """Simple test that doesn't need any infrastructure."""
    print("=" * 60)
    print("Simple Test - No Infrastructure Needed")
    print("=" * 60)
    
    # Basic assertions
    assert 1 + 1 == 2, "Math works!"
    print("✓ Basic math: 1 + 1 = 2")
    
    # String operations
    text = "Hello, Docker!"
    assert text.startswith("Hello"), "String check failed"
    print(f"✓ String test: '{text}'")
    
    # List operations
    items = [1, 2, 3, 4, 5]
    assert sum(items) == 15, "Sum check failed"
    print(f"✓ List sum: {items} = {sum(items)}")
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    test()

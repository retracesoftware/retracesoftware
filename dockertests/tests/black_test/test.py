import black


def test_black_formatting():
    unformatted_code = "def my_function (a,b):\n    return(a+b)"
    expected_formatted_code = "def my_function(a, b):\n    return a + b\n"

    formatted_code = black.format_str(unformatted_code, mode=black.FileMode())

    assert formatted_code == expected_formatted_code, "Black did not format the code as expected"

    print("Formatted code:", flush=True)
    print(formatted_code, flush=True)


if __name__ == "__main__":
    print("=== black_test ===", flush=True)
    test_black_formatting()

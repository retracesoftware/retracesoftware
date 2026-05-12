import humanize


def main():
    print("=== humanize_test ===")
    number = humanize.intcomma(1234567)
    size = humanize.naturalsize(15360, binary=True)
    assert number == "1,234,567"
    assert size == "15.0 KiB"
    print(f"number={number}")
    print(f"size={size}")
    print("humanize ok")


if __name__ == "__main__":
    main()

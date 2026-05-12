from boltons.iterutils import bucketize, chunked


def main():
    print("=== boltons_iterutils_test ===")
    chunks = list(chunked([1, 2, 3, 4, 5], 2))
    buckets = bucketize(["ada", "alan", "grace", "guido"], key=lambda word: word[0])
    normalized = {key: sorted(value) for key, value in buckets.items()}

    assert chunks == [[1, 2], [3, 4], [5]]
    assert normalized == {"a": ["ada", "alan"], "g": ["grace", "guido"]}
    print(f"chunks={chunks}")
    print(f"buckets={sorted(normalized.items())}")
    print("boltons iterutils ok")


if __name__ == "__main__":
    main()

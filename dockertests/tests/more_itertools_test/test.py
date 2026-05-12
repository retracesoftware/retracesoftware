from more_itertools import chunked, pairwise, windowed


def main():
    print("=== more_itertools_test ===")
    chunks = list(chunked(range(7), 3))
    pairs = list(pairwise(["a", "b", "c"]))
    windows = list(windowed([1, 2, 3, 4], 3))

    assert chunks == [[0, 1, 2], [3, 4, 5], [6]]
    assert pairs == [("a", "b"), ("b", "c")]
    assert windows == [(1, 2, 3), (2, 3, 4)]
    print(f"chunks={chunks}")
    print(f"pairs={pairs}")
    print("more-itertools ok")


if __name__ == "__main__":
    main()

from sortedcontainers import SortedDict, SortedList


def main():
    print("=== sortedcontainers_test ===")
    values = SortedList([5, 1, 3])
    values.add(2)
    values.add(4)
    mapping = SortedDict({"b": 2, "a": 1, "c": 3})

    assert list(values) == [1, 2, 3, 4, 5]
    assert list(mapping.items()) == [("a", 1), ("b", 2), ("c", 3)]
    print(f"values={list(values)}")
    print(f"keys={','.join(mapping.keys())}")
    print("sortedcontainers ok")


if __name__ == "__main__":
    main()

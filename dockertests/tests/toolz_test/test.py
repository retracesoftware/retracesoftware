from toolz import compose, groupby, pipe


def main():
    print("=== toolz_test ===")
    words = ["ada", "alan", "grace", "guido", "barbara"]
    grouped = groupby(lambda word: word[0], words)
    sizes = {key: len(value) for key, value in grouped.items()}
    pipeline = pipe([1, 2, 3], sum, lambda value: value * 10)
    transform = compose(str.upper, lambda value: f"user:{value}")

    assert sizes == {"a": 2, "g": 2, "b": 1}
    assert pipeline == 60
    print(f"sizes={sorted(sizes.items())}")
    print(transform("ada"))
    print("toolz ok")


if __name__ == "__main__":
    main()

from concurrent.futures import ThreadPoolExecutor


def compute(value):
    return {"input": value, "square": value * value}


def main():
    print("=== concurrent_futures_threadpool_test ===")
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(compute, range(6)))

    squares = [item["square"] for item in results]
    assert squares == [0, 1, 4, 9, 16, 25]
    print(f"squares={squares}")
    print("concurrent futures threadpool ok")


if __name__ == "__main__":
    main()

import numpy as np


def main():
    a = np.array([1, 2, 3])
    b = np.array([4, 5, 6])
    c = a + b
    print("res", c, flush=True)
    assert (c == np.array([5, 7, 9])).all()


if __name__ == "__main__":
    print("=== numpy_test ===", flush=True)
    main()

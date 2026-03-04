import time

def test_time():
    n = 10000000
    start = time.time()
    for i in range(n):
        time.time()
        for _ in range(100):
            pass
    end = time.time()
    print((end - start) * 1000000000 / n, "ns per call")

test_time()
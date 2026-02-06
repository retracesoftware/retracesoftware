import numpy as np
from scipy import stats, linalg, integrate

def test_scipy_operations():
    # 1. Statistical Analysis: Compute mean, median, mode, and standard deviation of a dataset
    data = np.array([10, 20, 20, 30, 40, 50, 50, 50, 60, 70])
    mean = np.mean(data)
    median = np.median(data)
    mode = stats.mode(data, keepdims=True).mode[0]  # Updated for compatibility
    std_dev = np.std(data)

    print("Statistical Analysis:")
    print(f"Mean: {mean}, Median: {median}, Mode: {mode}, Standard Deviation: {std_dev}\n")

    # 2. Linear Algebra: Solve a system of linear equations
    # Example: 2x + 3y = 8 and 3x + y = 5
    coefficients = np.array([[2, 3], [3, 1]])
    constants = np.array([8, 5])
    solution = linalg.solve(coefficients, constants)

    print("Linear Algebra:")
    print(f"Solution to equations 2x + 3y = 8 and 3x + y = 5 -> x: {solution[0]}, y: {solution[1]}\n")

    # 3. Integration: Calculate the integral of a function
    # Example function: f(x) = x^2
    result, error = integrate.quad(lambda x: x**2, 0, 5)

    print("Integration:")
    print(f"Integral of x^2 from 0 to 5: {result} (with error estimate {error})")

if __name__ == "__main__":
    test_scipy_operations() 
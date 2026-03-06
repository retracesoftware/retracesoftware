"""Target script for breakpoint tests.

Has a clear function with identifiable lines for setting breakpoints.
"""
def add(a, b):
    result = a + b
    return result

x = add(3, 4)
y = add(10, 20)
print(x + y)

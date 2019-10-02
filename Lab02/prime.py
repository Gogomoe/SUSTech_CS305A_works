import math


def find_prime(start: int, end: int) -> list:
    if start < 2:
        start = 2
    res = list(range(start, end + 1))
    for i in range(2, math.ceil(math.sqrt(end + 1))):
        res = list(filter(lambda x: x % i != 0 or x == i, res))
    return res

"""Chart calculations use broker candle closes, with no synthetic price history."""

from itertools import pairwise
from math import sqrt


def moving_average(values: list[float], period: int = 20) -> list[float | None]:
    return [
        sum(values[i - period + 1 : i + 1]) / period if i >= period - 1 else None
        for i in range(len(values))
    ]


def bollinger_bands(values: list[float], period: int = 20) -> tuple[list, list]:
    means = moving_average(values, period)
    upper, lower = [], []
    for i, mean in enumerate(means):
        if mean is None:
            upper.append(None)
            lower.append(None)
        else:
            deviation = sqrt(sum((v - mean) ** 2 for v in values[i - period + 1 : i + 1]) / period)
            upper.append(mean + 2 * deviation)
            lower.append(mean - 2 * deviation)
    return upper, lower


def relative_strength(values: list[float], period: int = 14) -> list[float | None]:
    result = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [b - a for a, b in pairwise(values)]
    gain = sum(max(v, 0) for v in changes[:period]) / period
    loss = sum(max(-v, 0) for v in changes[:period]) / period
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        result[index] = (
            50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        )
    return result

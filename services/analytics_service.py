from __future__ import annotations


def calculate_average(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def calculate_median(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]

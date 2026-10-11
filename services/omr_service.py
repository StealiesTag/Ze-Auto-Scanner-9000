from __future__ import annotations


def choose_option_from_bubbles(bubble_values: dict[str, float], threshold: float = 0.38) -> tuple[str, str, float]:
    options = ["A", "B", "C", "D"]
    filled = {key: float(value) for key, value in bubble_values.items() if key in options}

    if not filled:
        return "blank", "blank", 0.0

    max_option, max_value = max(filled.items(), key=lambda item: item[1])
    if max_value < threshold:
        return "blank", "blank", max_value

    if sum(1 for value in filled.values() if value >= threshold) > 1:
        return "multiple_marks", "multiple_marks", max_value

    return max_option, "detected", max_value

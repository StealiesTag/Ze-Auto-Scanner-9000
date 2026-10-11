from __future__ import annotations


def recommend_learning_objectives(response_rates: dict[str, float], threshold: float = 0.4) -> list[str]:
    flagged = []
    for objective, error_rate in response_rates.items():
        if error_rate >= threshold:
            flagged.append(objective)
    return flagged

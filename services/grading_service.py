from __future__ import annotations


def calculate_score(correct_key: dict[int, str], student_answers: dict[int, str]) -> dict:
    total_questions = len(correct_key)
    correct = 0
    incorrect = 0
    unanswered = 0
    earned_points = 0.0
    total_points = 0.0

    for question_number, answer_key in correct_key.items():
        total_points += 1.0
        student_answer = student_answers.get(question_number)
        if not student_answer:
            unanswered += 1
            continue
        if student_answer.upper() == answer_key.upper():
            correct += 1
            earned_points += 1.0
        else:
            incorrect += 1

    percentage = round((earned_points / total_points) * 100, 2) if total_points else 0.0
    return {
        "correct": correct,
        "incorrect": incorrect,
        "unanswered": unanswered,
        "earned_points": earned_points,
        "total_points": total_points,
        "percentage": percentage,
    }

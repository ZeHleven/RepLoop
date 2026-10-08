"""Narrow deterministic checks; these do not claim general semantic correctness."""
import re
from datetime import date, timedelta


def history_scope_claim_is_consistent(reply: str, data: dict) -> bool:
    dates = [date.fromisoformat(item["date"]) for item in data["days"]]
    if not all(day.isoformat() in reply for day in dates):
        return False
    as_of = date.fromisoformat(data["scope"]["as_of"])
    for match in re.finditer(r"(?:最近|近)\s*(\d+)\s*天(?:内)?", reply):
        lower = as_of - timedelta(days=int(match.group(1)) - 1)
        if any(day < lower or day > as_of for day in dates):
            return False
    return True


def today_calories_appear(reply: str, data: dict) -> bool:
    # Check the observed aggregate rather than a hardcoded expected answer.
    numbers = re.findall(r"(\d+(?:\.\d+)?)\s*(?:千卡|大卡|kcal)", reply, flags=re.I)
    return any(abs(float(number) - data["total_calories"]) < 0.11 for number in numbers)

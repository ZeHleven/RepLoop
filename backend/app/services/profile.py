def calculate_bmi(height_cm: float, weight_kg: float) -> tuple[float, str]:
    bmi = round(weight_kg / ((height_cm / 100) ** 2), 1)
    if bmi < 18.5:
        category = "偏瘦"
    elif bmi < 24.0:
        category = "正常"
    elif bmi < 28.0:
        category = "超重"
    else:
        category = "肥胖"
    return bmi, category

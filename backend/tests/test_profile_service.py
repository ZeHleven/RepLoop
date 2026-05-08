from app.services.profile import calculate_bmi


def test_bmi_underweight():
    bmi, category = calculate_bmi(height_cm=175, weight_kg=52)
    assert bmi == round(52 / (1.75 ** 2), 1)
    assert category == "偏瘦"


def test_bmi_normal():
    bmi, category = calculate_bmi(height_cm=175, weight_kg=70)
    assert category == "正常"


def test_bmi_overweight():
    bmi, category = calculate_bmi(height_cm=170, weight_kg=80)
    assert category == "超重"


def test_bmi_obese():
    bmi, category = calculate_bmi(height_cm=170, weight_kg=100)
    assert category == "肥胖"

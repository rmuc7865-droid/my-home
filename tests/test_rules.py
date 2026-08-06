from server.rules import Rule, evaluate_rule


def test_numeric_rule_satisfied():
    rule = Rule(name="SOC", system="x1", field="battery_soc", operator=">=", value=20)
    assert evaluate_rule(rule, {"battery_soc": 21}) == (True, 21)


def test_numeric_rule_fails_when_missing():
    rule = Rule(name="SOC", system="x1", field="battery_soc", operator=">=", value=20)
    assert evaluate_rule(rule, {}) == (False, None)


def test_exists_rule():
    rule = Rule(name="PV", system="x1", field="pv_power", operator="exists")
    assert evaluate_rule(rule, {"pv_power": 0}) == (True, 0)

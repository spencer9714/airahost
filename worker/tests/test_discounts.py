import pytest
from worker.core.discounts import apply_discount

def make_policy(stacking_mode="compound", weekly_pct=0, monthly_pct=0, non_ref_pct=0, max_pct=100):
    """Helper to create a discount policy dict."""
    return {
        "stackingMode": stacking_mode,
        "weeklyDiscountPct": weekly_pct,
        "monthlyDiscountPct": monthly_pct,
        "nonRefundableDiscountPct": non_ref_pct,
        "maxTotalDiscountPct": max_pct
    }

def test_no_discounts():
    policy = make_policy()
    res = apply_discount(100, 3, policy)
    assert res["refundablePrice"] == 100
    assert res["nonRefundablePrice"] == 100

def test_weekly_discount():
    # 10% weekly discount for 7+ days
    policy = make_policy(weekly_pct=10)
    res = apply_discount(100, 7, policy)
    assert res["refundablePrice"] == 90
    # Non-refundable usually inherits length discount if NR discount is 0
    assert res["nonRefundablePrice"] == 90

def test_monthly_overrides_weekly():
    # 10% weekly, 20% monthly. Stay is 30 days.
    policy = make_policy(weekly_pct=10, monthly_pct=20)
    res = apply_discount(100, 30, policy)
    assert res["refundablePrice"] == 80

def test_stacking_compound():
    # 10% length, 10% non-ref. Compound: 1 - (0.9 * 0.9) = 19% off
    policy = make_policy(stacking_mode="compound", weekly_pct=10, non_ref_pct=10)
    res = apply_discount(100, 7, policy)
    assert res["refundablePrice"] == 90
    assert res["nonRefundablePrice"] == 81

def test_stacking_additive():
    # 10% length, 10% non-ref. Additive: 10 + 10 = 20% off
    policy = make_policy(stacking_mode="additive", weekly_pct=10, non_ref_pct=10)
    res = apply_discount(100, 7, policy)
    assert res["refundablePrice"] == 90
    assert res["nonRefundablePrice"] == 80

def test_stacking_best_only():
    # 10% length, 20% non-ref. Best only: max(10, 20) = 20% off
    policy = make_policy(stacking_mode="best_only", weekly_pct=10, non_ref_pct=20)
    res = apply_discount(100, 7, policy)
    assert res["refundablePrice"] == 90
    assert res["nonRefundablePrice"] == 80

def test_max_discount_cap():
    # 50% length + 50% non-ref (additive) = 100% off.
    # Capped at 60%.
    policy = make_policy(stacking_mode="additive", weekly_pct=50, non_ref_pct=50, max_pct=60)
    res = apply_discount(100, 7, policy)
    assert res["nonRefundablePrice"] == 40

def test_short_stay_ignores_length_discount():
    # 50% weekly discount, but stay is only 3 days
    policy = make_policy(weekly_pct=50)
    res = apply_discount(100, 3, policy)
    assert res["refundablePrice"] == 100
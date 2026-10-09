"""Pure formula extracted from the supplied scorer, without database or network imports.

Both legacy replay and pilot inputs share these functions. Thresholds, arithmetic
order, clipping and rounding intentionally preserve the reference implementation.
"""


def score_row(r, timeline):
    clean_title = r["clean_title"]
    avg_heat = r["avg_heat"]
    peak_heat = r["peak_heat"]
    momentum = r["momentum"]
    platform_count = r["platform_count"]
    has_promo_tag = r["has_promo_tag"]
    heat_score = min(100.0, avg_heat * 0.7 + peak_heat * 0.3)
    momentum_score = max(0.0, min(100.0, 50.0 + momentum * 0.5))
    if timeline:
        active_days = sum(1 for p in timeline if p.get("value", 0) > 0)
        recent_3d = [p.get("value", 0) for p in timeline[-3:]]
        recent_3d_avg = sum(recent_3d) / max(len(recent_3d), 1)
    else:
        active_days = 0
        recent_3d_avg = avg_heat
    if active_days <= 2 and peak_heat > 0:
        heat_score *= 0.4
        momentum_score = min(momentum_score, 30.0)
    elif active_days <= 5 and peak_heat > 0:
        damping = active_days / 6.0
        heat_score *= 0.5 + 0.5 * damping
        momentum_score = 50.0 + (momentum_score - 50.0) * damping
    if recent_3d_avg == 0 and momentum > 0:
        momentum_score = min(momentum_score, 25.0)
    if avg_heat < 5.0 and momentum > 0:
        momentum_score = min(momentum_score, 40.0)
    if platform_count >= 3:
        platform_score = 100.0
    elif platform_count == 2:
        platform_score = 80.0
    else:
        platform_score = 30.0
    promo_tags = r["promo_tags"]
    is_hot = any(t in ("Trending", "Hot", "Must-sees", "Top") for t in promo_tags)
    promo_score = 100.0 if is_hot else 60.0 if has_promo_tag else 25.0
    total_score = 0.35 * heat_score + 0.3 * momentum_score + 0.2 * platform_score + 0.15 * promo_score
    total_score = round(min(100.0, max(0.0, total_score)), 1)
    if total_score >= 75.0:
        tier = "S"
        tier_name = "🚀 爆款强推"
    elif total_score >= 60.0:
        tier = "A"
        tier_name = "📈 潜力黑马"
    elif total_score >= 45.0:
        tier = "B"
        tier_name = "🛡️ 长青稳健"
    else:
        tier = "C"
        tier_name = "⏳ 观望候选"
    pred = evaluate_prediction(timeline)
    return {
        "clean_title": clean_title,
        "score": total_score,
        "tier": tier,
        "tier_name": tier_name,
        "heat_score": round(heat_score, 1),
        "momentum_score": round(momentum_score, 1),
        "platform_score": round(platform_score, 1),
        "promo_score": round(promo_score, 1),
        "prediction_label": pred["prediction_label"],
        "breakout_score": pred["breakout_score"],
        "velocity_24h": pred["velocity_24h"],
        "acceleration": pred["acceleration"],
        "climb_days": pred["climb_days"],
    }


def evaluate_prediction(timeline):
    """
    Calculates Early Breakout Metrics with Multi-Peak & Sustained Liveness Verification.
    Strictly filters out isolated single-hour/single-day flash spikes (e.g. Orc love, The Vengeance of Her).
    """
    if not timeline or len(timeline) < 7:
        return {
            "prediction_label": "⏳ 正常平稳",
            "breakout_score": 0.0,
            "velocity_24h": 0.0,
            "acceleration": 0.0,
            "climb_days": 0,
        }
    vals = [float(p.get("value", 0)) for p in timeline]
    h_t = vals[-1]
    h_prev1 = vals[-2]
    h_prev2 = vals[-3]
    v_24h = round(h_t - h_prev1, 1)
    accel = round(h_t - h_prev1 - (h_prev1 - h_prev2), 1)
    climb_days = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1]:
            climb_days += 1
        elif vals[i] == vals[i - 1] and vals[i] > 0:
            continue
        else:
            break
    if h_t <= 0:
        return {
            "prediction_label": "⏳ 正常平稳",
            "breakout_score": 0.0,
            "velocity_24h": v_24h,
            "acceleration": accel,
            "climb_days": climb_days,
        }
    active_days = sum(1 for p in vals if p > 0)
    avg_heat = sum(vals) / max(len(vals), 1)
    last7 = vals[-7:]
    zeros_in_last7 = sum(1 for v in last7 if v <= 0)
    if zeros_in_last7 >= 2:
        return {
            "prediction_label": "⏳ 正常平稳",
            "breakout_score": 0.0,
            "velocity_24h": v_24h,
            "acceleration": accel,
            "climb_days": climb_days,
        }
    if active_days < 10 or avg_heat < 15.0:
        return {
            "prediction_label": "⏳ 正常平稳",
            "breakout_score": 0.0,
            "velocity_24h": v_24h,
            "acceleration": accel,
            "climb_days": climb_days,
        }
    is_multi_day_climb = climb_days >= 2 and v_24h > 0
    is_sustained_plateau = h_t >= 50.0 and h_prev1 >= 45.0
    if not (is_multi_day_climb or is_sustained_plateau):
        return {
            "prediction_label": "⏳ 正常平稳",
            "breakout_score": 0.0,
            "velocity_24h": v_24h,
            "acceleration": accel,
            "climb_days": climb_days,
        }
    base_bonus = min(20.0, h_t * 0.2)
    score_raw = max(0.0, v_24h * 0.45 + accel * 0.35 + climb_days * 5.0 + base_bonus)
    breakout_score = round(min(100.0, score_raw), 1)
    if v_24h >= 20.0 and accel > 0 and (climb_days >= 2):
        label = "⚡ 24h突发引爆"
    elif accel >= 12.0 and h_t > h_prev1 and (climb_days >= 2):
        label = "🚨 拐点起飞"
    elif climb_days >= 3 and h_t >= 15.0:
        label = "📈 潜伏连涨"
    elif h_t >= 50.0 and v_24h >= -5.0:
        label = "🔥 高位稳定"
    else:
        label = "⏳ 正常平稳"
    return {
        "prediction_label": label,
        "breakout_score": breakout_score,
        "velocity_24h": v_24h,
        "acceleration": accel,
        "climb_days": climb_days,
    }

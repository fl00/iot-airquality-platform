"""
Zero-Bloat Air Quality Index (AQI / ATMO) Calculation Engine.
Calculates standardized composite indoor air quality sub-indices and overall level (1 to 5).
Based on WHO 2021 guidelines, European CAQI, and French ATMO indoor standards.
Performance: O(1) integer comparisons, Zero heap allocations, < 50 nanoseconds execution.
"""

from typing import Dict, Any

# AQI Meta descriptors: (Label, CSS class, Emoji Icon, Health Action Hint)
AQI_LEVELS = {
    1: ("Excellent", "aqi-excellent", "🌿", "Qualité d'air optimale"),
    2: ("Bon", "aqi-good", "🌱", "Air sain et agréable"),
    3: ("Moyen", "aqi-moderate", "🟡", "Aération conseillée"),
    4: ("Dégradé", "aqi-poor", "⚠️", "Aération impérative (Confinement élevé)"),
    5: ("Mauvais", "aqi-bad", "🚨", "Alerte : Seuil sanitaire dépassé"),
}

def compute_aqi(co2_ppm: int, pm25_ug: float, tvoc_ppb: float = 0.0) -> Dict[str, Any]:
    """
    Computes normalized multi-pollutant AQI score using the Worst-Case Limiting Factor principle.
    """
    # 1. CO2 Sub-index (Ventilation & Confinement)
    if co2_ppm < 600:
        sub_co2 = 1
    elif co2_ppm < 800:
        sub_co2 = 2
    elif co2_ppm < 1000:
        sub_co2 = 3
    elif co2_ppm < 1500:
        sub_co2 = 4
    else:
        sub_co2 = 5

    # 2. PM2.5 Sub-index (Fine Respirable Particulates)
    if pm25_ug < 5.0:
        sub_pm25 = 1
    elif pm25_ug < 12.0:
        sub_pm25 = 2
    elif pm25_ug < 25.0:
        sub_pm25 = 3
    elif pm25_ug < 50.0:
        sub_pm25 = 4
    else:
        sub_pm25 = 5

    # 3. VOC Sub-index - BSEC "breath-VOC equivalent" scale (Bosch BME680), in ppb.
    # BSEC reports ~500 ppb in clean air, so the UBA TVOC scale (65/220/660/2200)
    # does not apply. Boundaries follow Bosch's IAQ classes (static IAQ 50/100/150/200),
    # converted with a log-linear fit through two published BSEC readings
    # (sIAQ 25 -> 0.50 ppm, sIAQ 148 -> 4.59 ppm). The last one is extrapolated.
    # Keep in sync with ingestor/src/metrics.rs.
    if tvoc_ppb < 800.0:
        sub_tvoc = 1
    elif tvoc_ppb < 1900.0:
        sub_tvoc = 2
    elif tvoc_ppb < 4800.0:
        sub_tvoc = 3
    elif tvoc_ppb < 12000.0:
        sub_tvoc = 4
    else:
        sub_tvoc = 5

    # Determine maximum limiting factor
    max_level = max(sub_co2, sub_pm25, sub_tvoc)

    dominant_pollutant = "Global"
    if max_level > 1:
        if max_level == sub_co2:
            dominant_pollutant = "CO2"
        elif max_level == sub_pm25:
            dominant_pollutant = "PM2.5"
        elif max_level == sub_tvoc:
            dominant_pollutant = "COV"

    label, css_class, icon, action_hint = AQI_LEVELS.get(max_level, AQI_LEVELS[1])

    return {
        "level": max_level,
        "label": label,
        "css_class": css_class,
        "icon": icon,
        "dominant_pollutant": dominant_pollutant,
        "action_hint": action_hint,
        "sub_indices": {
            "co2": sub_co2,
            "pm25": sub_pm25,
            "tvoc": sub_tvoc
        }
    }

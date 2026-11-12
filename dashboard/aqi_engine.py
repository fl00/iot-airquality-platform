"""
Display metadata for the air quality level (1 to 5).

The level itself is computed once, by the ingestor (`compute_aqi_level` in
ingestor/src/metrics.rs), and delivered in every stream event as `aqi_level`.
Thresholds are documented in docs/AIR_QUALITY_GUIDE.md.
"""

from typing import Any, Dict

# level: (label, CSS class, icon, advice)
AQI_LEVELS = {
    1: ("Excellent", "aqi-excellent", "🌿", "Qualité d'air optimale"),
    2: ("Bon", "aqi-good", "🌱", "Air sain et agréable"),
    3: ("Moyen", "aqi-moderate", "🟡", "Aération conseillée"),
    4: ("Dégradé", "aqi-poor", "⚠️", "Aération impérative (Confinement élevé)"),
    5: ("Mauvais", "aqi-bad", "🚨", "Alerte : Seuil sanitaire dépassé"),
}


def aqi_meta(level: int) -> Dict[str, Any]:
    label, css_class, icon, action_hint = AQI_LEVELS.get(level, AQI_LEVELS[1])
    return {"level": level, "label": label, "css_class": css_class, "icon": icon, "action_hint": action_hint}

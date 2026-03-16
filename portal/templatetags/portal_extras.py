from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except Exception:
        return None


@register.filter
def luna_an(value):
    """Afișează o dată ca "Octombrie 2026" (termene PNA la nivel de lună)."""
    if not value:
        return ""
    try:
        m = int(getattr(value, "month", 0))
        y = int(getattr(value, "year", 0))
    except Exception:
        return ""

    months = {
        1: "Ianuarie",
        2: "Februarie",
        3: "Martie",
        4: "Aprilie",
        5: "Mai",
        6: "Iunie",
        7: "Iulie",
        8: "August",
        9: "Septembrie",
        10: "Octombrie",
        11: "Noiembrie",
        12: "Decembrie",
    }
    if not m or not y:
        return ""
    return f"{months.get(m, str(m))} {y}"

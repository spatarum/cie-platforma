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



@register.filter
def role_label(user):
    try:
        if not user:
            return ""
        if getattr(user, "is_superuser", False):
            return "Administrator"
        if getattr(user, "is_staff", False):
            profil = getattr(user, "profil_expert", None)
            if profil and getattr(profil, "este_staff_comisie", False):
                return "Staff comisie"
            return "Staff"
        return "Expert"
    except Exception:
        return ""


@register.filter
def display_name(user):
    try:
        return user.get_full_name() or user.username
    except Exception:
        return ""

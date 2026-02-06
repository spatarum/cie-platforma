def expert_ui_prefs(request):
    """Expune preferințele UI ale expertului către template-uri."""
    text_mare = False
    try:
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_staff:
            profil = getattr(user, "profil_expert", None)
            if profil:
                text_mare = bool(getattr(profil, "pref_text_mare", False))
    except Exception:
        text_mare = False
    return {"expert_text_mare": text_mare}

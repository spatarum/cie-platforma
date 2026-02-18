def expert_ui_prefs(request):
    """Expune preferințele UI către template-uri (Expert + Staff).

    Preferințele sunt stocate în ExpertProfile.pref_text_mare.
    Pentru Staff, profilul este creat la prima salvare a preferințelor.
    """
    text_mare = False
    try:
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            profil = getattr(user, "profil_expert", None)
            if profil:
                text_mare = bool(getattr(profil, "pref_text_mare", False))
    except Exception:
        text_mare = False
    return {"expert_text_mare": text_mare}

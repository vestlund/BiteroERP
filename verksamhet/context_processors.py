from . import halsokontroll


def halsa(request):
    """Anmärkningar från senaste startkontrollen visas överst på varje sida."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"halsa": halsokontroll.senaste()}

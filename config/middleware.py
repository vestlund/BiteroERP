from django.conf import settings
from django.contrib.auth.views import redirect_to_login


class LoginRequiredEverywhere:
    """Kräver inloggning för alla sidor utom inloggningen och statiska filer."""

    OPEN_PREFIXES = ("/admin/login", "/admin/logout", "/static/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(self.OPEN_PREFIXES):
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return self.get_response(request)

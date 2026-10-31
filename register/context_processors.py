from .models import Foretag


def foretag(request):
    data = {"foretag": None, "bank_att_hantera": 0}
    try:
        data["foretag"] = Foretag.hamta()
        if request.user.is_authenticated:
            from bokforing.models import Bankpost
            data["bank_att_hantera"] = Bankpost.objects.filter(verifikation__isnull=True, ignorerad=False).count()
    except Exception:  # t.ex. innan migreringar körts
        pass
    return data

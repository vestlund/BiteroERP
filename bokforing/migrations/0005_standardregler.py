from django.db import migrations


def skapa(apps, schema_editor):
    Regel = apps.get_model("bokforing", "Matchningsregel")
    Konto = apps.get_model("register", "Konto")
    for namn, text, riktning, konto in [
        ("Skattekontot", "skatteverket", "bada", 1630),
        ("Bankavgift", "avgift", "ut", 6570),
    ]:
        if Konto.objects.filter(pk=konto).exists() and not Regel.objects.filter(text_innehaller=text).exists():
            Regel.objects.create(namn=namn, text_innehaller=text, riktning=riktning, motkonto_id=konto,
                                 automatisk=False)


class Migration(migrations.Migration):
    dependencies = [("bokforing", "0004_bankpost_automatisk_bankpost_end_to_end_and_more"),
                    ("register", "0002_foretag_last_tom_foretag_momsperiod_and_more")]
    operations = [migrations.RunPython(skapa, migrations.RunPython.noop)]

from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def kr(v, decimaler=2):
    """12345.5 -> '12 345,50'"""
    if v in (None, ""):
        return ""
    v = Decimal(v)
    s = f"{v:,.{int(decimaler)}f}"
    return s.replace(",", " ").replace(".", ",")


@register.filter
def h(v):
    """Timmar utan onödiga decimaler: 8.00 -> '8', 7.50 -> '7,5'"""
    if v in (None, "") or Decimal(v) == 0:
        return ""
    s = f"{Decimal(v):f}".rstrip("0").rstrip(".") if "." in f"{Decimal(v):f}" else f"{Decimal(v):f}"
    return s.replace(".", ",")


@register.filter
def get(d, k):
    return d.get(k) if d else None


@register.filter
def neg(v):
    return -Decimal(v or 0)


@register.filter
def summa(lista):
    return sum((Decimal(v or 0) for v in lista), Decimal(0))


@register.filter
def sub(a, b):
    """a minus b; b får vara en lista (summeras)."""
    if isinstance(b, (list, tuple)):
        b = summa(b)
    return Decimal(a or 0) - Decimal(b or 0)


@register.filter
def index(lista, i):
    try:
        return lista[int(i)]
    except (IndexError, TypeError, ValueError):
        return ""


@register.filter
def procent(a, b):
    try:
        return Decimal(a) / Decimal(b) * 100 if b else None
    except Exception:
        return None

"""Lettura degli importi scritti all'italiana nei form."""

import re
from decimal import Decimal, InvalidOperation

_MIGLIAIA = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


def importo_italiano(testo: str | None) -> Decimal | None:
    """«45.000,50» → 45000.50; «900.000» → 900000; «1234.5» → 1234.5; «€ 12 000» → 12000.

    Con la virgola, i punti sono separatori delle migliaia. Senza virgola, i punti sono
    migliaia solo se separano gruppi di tre cifre (900.000), altrimenti sono il decimale.
    Restituisce None se il testo è vuoto o non è un numero.
    """
    if testo is None:
        return None
    s = testo.replace("€", "").replace(" ", "").replace(" ", "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif _MIGLIAIA.match(s):
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None

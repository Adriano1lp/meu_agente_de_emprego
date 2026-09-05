from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

API_DIR = Path(__file__).resolve().parents[1]
load_dotenv(API_DIR / ".env")


def main() -> None:
    secret = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not secret:
        raise SystemExit("Defina STRIPE_SECRET_KEY antes de criar o preco Essencial.")

    try:
        import stripe
    except ModuleNotFoundError as exc:
        raise SystemExit("Instale as dependencias: pip install -r requirements.txt") from exc

    stripe.api_key = secret
    product = stripe.Product.create(
        name="Essencial",
        description="Plano Essencial do Meu Agente de Emprego — cota ampliada de IA.",
    )
    price = stripe.Price.create(
        product=product.id,
        unit_amount=1990,
        currency="brl",
        recurring={"interval": "month"},
        nickname="essencial-monthly",
    )
    print(f"Product: {product.id}")
    print(f"STRIPE_PRICE_ESSENCIAL={price.id}")


if __name__ == "__main__":
    sys.exit(main())

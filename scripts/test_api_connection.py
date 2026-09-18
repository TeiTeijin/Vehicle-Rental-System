from app.services import carimages_client as ci


def _printable(url: str) -> str:
    return url.split("?", 1)[0]


def test_signed_urls() -> None:
    print("== Signed URLs (CI_API_KEY only) ==")
    car = ci.get_signed_image_url("BMW", "3 Series", 2022)
    print("  car:", _printable(car), "...")
    assert car.startswith("https://carimagesapi.com/image?")

    moto = ci.get_signed_image_url("Yamaha", "NMAX", 2022, type="moto")
    print("  moto:", _printable(moto), "...")
    assert moto.startswith("https://carimagesapi.com/image?")
    print("OK\n")


def test_rest_catalog() -> None:
    print("== REST catalog (CI_API_KEY + secret) ==")

    gen = ci.resolve_generation_slug("BMW", "3 Series", 2022)
    print("  BMW 3 Series 2022 generation:", gen)
    assert gen is not None

    image = ci.get_vehicle_image_payload("BMW", "3 Series", gen)
    views = list(image.get("views", {}).keys())
    print("  views:", views)
    assert views, "expected camera views"

    model = ci.get_vehicle_model_3d_payload("BMW", "3 Series", gen)
    print("  3D model keys:", list(model.keys()))
    print("OK\n")


def test_seed_vehicles() -> None:
    print("== Generation resolution for seeded vehicles ==")
    vehicles = [
        ("Toyota", "Vios", 2022),
        ("Honda", "Civic", 2021),
        ("Toyota", "Camry", 2020),
    ]
    for make, model, year in vehicles:
        gen = ci.resolve_generation_slug(make, model, year)
        print(f"  {make} {model} {year}: {gen}")
        assert gen is not None
    print("OK\n")


def main() -> None:
    test_signed_urls()
    test_rest_catalog()
    test_seed_vehicles()


if __name__ == "__main__":
    main()
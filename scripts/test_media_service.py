from urllib.parse import urlsplit

from app.database import SessionLocal
from app.models import Vehicle, Vehicle_Media
from app.services import media_service


def _printable(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def main() -> None:
    session = SessionLocal()
    try:
        vehicles = session.query(Vehicle).all()
        print(f"Processing {len(vehicles)} vehicles...\n")

        for vehicle in vehicles:
            kind = "moto" if media_service.is_motorcycle(vehicle) else "car"
            rows = media_service.get_or_fetch_media(session, vehicle)
            print(f"[{kind}] {vehicle.make} {vehicle.model} {vehicle.year}")
            for row in rows:
                if row.view_angle == "model_3d":
                    print(f"    3D : {_printable(row.model_3d_url)}")
                else:
                    print(f"    img: {_printable(row.image_url)}")

        session.commit()

        print("\n== Re-running to verify cache (no network expected) ==")
        count_before = session.query(Vehicle_Media.media_id).count()
        for vehicle in vehicles:
            media_service.get_or_fetch_media(session, vehicle)
        session.commit()
        count_after = session.query(Vehicle_Media.media_id).count()
        print(f"media rows before={count_before} after={count_after}")
        assert count_before == count_after, "cache miss: rows were rewritten"
        print("CACHE OK - second pass hit the DB, no API calls")
    finally:
        session.close()


if __name__ == "__main__":
    main()
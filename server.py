import os
from dataclasses import asdict

from mcp.server.fastmcp import FastMCP
from fast_flights import FlightQuery, Passengers, create_query, get_flights

mcp = FastMCP(
    "google-flights-mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 3000)),
    stateless_http=True,
    json_response=True,
)


def _fmt_date(date_tuple):
    if not date_tuple:
        return None
    try:
        return "-".join(str(x) for x in date_tuple if x is not None)
    except Exception:
        return None


def _fmt_time(time_tuple):
    if not time_tuple:
        return None
    try:
        return ":".join(f"{int(x):02d}" for x in time_tuple if x is not None)
    except Exception:
        return None


def _serialize_leg(leg):
    try:
        return {
            "from_airport": getattr(leg.from_airport, "code", None),
            "from_airport_name": getattr(leg.from_airport, "name", None),
            "to_airport": getattr(leg.to_airport, "code", None),
            "to_airport_name": getattr(leg.to_airport, "name", None),
            "departure": {
                "date": _fmt_date(getattr(leg.departure, "date", None)),
                "time": _fmt_time(getattr(leg.departure, "time", None)),
            },
            "arrival": {
                "date": _fmt_date(getattr(leg.arrival, "date", None)),
                "time": _fmt_time(getattr(leg.arrival, "time", None)),
            },
            "duration_minutes": getattr(leg, "duration", None),
            "plane_type": getattr(leg, "plane_type", None),
        }
    except Exception as e:
        return {"error": f"leg parse error: {e}"}


def _serialize(result):
    itineraries = []
    for f in result:
        try:
            itineraries.append(
                {
                    "type": f.type,
                    "price": f.price,
                    "airlines": f.airlines,
                    "carbon_emission_grams": getattr(f.carbon, "emission", None) if f.carbon else None,
                    "legs": [_serialize_leg(leg) for leg in f.flights],
                }
            )
        except Exception as e:
            itineraries.append({"error": f"itinerary parse error: {e}"})
    meta = getattr(result, "metadata", None)
    try:
        airlines_meta = [asdict(a) for a in meta.airlines] if meta else []
    except Exception:
        airlines_meta = []
    return {"itineraries": itineraries, "count": len(itineraries), "airlines": airlines_meta}


@mcp.tool()
def search_flights(
    from_airport: str,
    to_airport: str,
    date: str,
    return_date: str = "",
    adults: int = 1,
    children: int = 0,
    seat: str = "economy",
    currency: str = "RUB",
) -> dict:
    """Search real Google Flights offers for a route.

    Args:
        from_airport: 3-letter IATA code of the departure airport, e.g. "LED".
        to_airport: 3-letter IATA code of the arrival airport, e.g. "TBS".
        date: departure date in YYYY-MM-DD format.
        return_date: return date in YYYY-MM-DD format. Leave empty ("") for a one-way search.
        adults: number of adult passengers (default 1).
        children: number of child passengers (default 0).
        seat: cabin class - one of "economy", "premium-economy", "business", "first".
        currency: 3-letter currency code for prices, e.g. "RUB", "USD", "EUR" (default "RUB").
    """
    flights = [FlightQuery(date=date, from_airport=from_airport, to_airport=to_airport)]
    trip = "one-way"
    if return_date:
        trip = "round-trip"
        flights.append(
            FlightQuery(date=return_date, from_airport=to_airport, to_airport=from_airport)
        )

    query = create_query(
        flights=flights,
        trip=trip,
        seat=seat,
        passengers=Passengers(adults=adults, children=children),
        currency=currency,
    )

    try:
        result = get_flights(query)
    except Exception as e:
        return {"error": str(e), "itineraries": [], "count": 0, "currency": currency}

    out = _serialize(result)
    out["currency"] = currency
    return out


@mcp.tool()
def health() -> dict:
    """Basic health check."""
    return {"ok": True}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

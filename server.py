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


def _serialize(result):
    itineraries = []
    for f in result:
        itineraries.append(
            {
                "type": f.type,
                "price": f.price,
                "airlines": f.airlines,
                "carbon_emission_grams": f.carbon.emission if f.carbon else None,
                "legs": [
                    {
                        "from_airport": leg.from_airport.code,
                        "from_airport_name": leg.from_airport.name,
                        "to_airport": leg.to_airport.code,
                        "to_airport_name": leg.to_airport.name,
                        "departure": {
                            "date": "-".join(str(x) for x in leg.departure.date),
                            "time": ":".join(f"{x:02d}" for x in leg.departure.time),
                        },
                        "arrival": {
                            "date": "-".join(str(x) for x in leg.arrival.date),
                            "time": ":".join(f"{x:02d}" for x in leg.arrival.time),
                        },
                        "duration_minutes": leg.duration,
                        "plane_type": leg.plane_type,
                    }
                    for leg in f.flights
                ],
            }
        )
    meta = getattr(result, "metadata", None)
    airlines_meta = [asdict(a) for a in meta.airlines] if meta else []
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
    )

    try:
        result = get_flights(query)
    except Exception as e:
        return {"error": str(e), "itineraries": [], "count": 0}

    return _serialize(result)


@mcp.tool()
def health() -> dict:
    """Basic health check."""
    return {"ok": True}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

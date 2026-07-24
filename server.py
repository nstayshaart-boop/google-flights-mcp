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
    # Protobuf/proto3 treats 0 as "unset", so a true 0 (e.g. exactly HH:00
    # or 00:MM) can arrive as None here. Since a time always has both an
    # hour and a minute, missing components must be treated as 0, not
    # dropped - otherwise "22:00" silently becomes "22" and "00:30"
    # silently becomes "30", which is misleading, not just cosmetic.
    if time_tuple is None:
        return None
    try:
        parts = list(time_tuple)
        if len(parts) < 2:
            parts = parts + [0] * (2 - len(parts))
        hour, minute = parts[0], parts[1]
        hour = 0 if hour is None else int(hour)
        minute = 0 if minute is None else int(minute)
        return f"{hour:02d}:{minute:02d}"
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


def _run_one_way(from_airport, to_airport, date, adults, children, seat, currency):
    query = create_query(
        flights=[FlightQuery(date=date, from_airport=from_airport, to_airport=to_airport)],
        trip="one-way",
        seat=seat,
        passengers=Passengers(adults=adults, children=children),
        currency=currency,
    )
    result = get_flights(query)
    return _serialize(result)


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

    For a one-way search (return_date left empty), returns {itineraries, count, currency}.

    For a round trip (return_date set), Google Flights only exposes outbound-leg data in a
    single combined round-trip query (the return leg requires a second, separate step on
    google's own site too). To actually give both directions, this runs two one-way searches
    (outbound and return) and pairs them up by airline where possible. Returns
    {round_trip: true, currency, pairs, count}, where each item in `pairs` has
    `airline`, `total_price` (outbound + return, already scaled for the requested
    passenger count), `outbound`, and `return` (each an itinerary dict with legs/times/etc).

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
    try:
        if not return_date:
            out = _run_one_way(from_airport, to_airport, date, adults, children, seat, currency)
            out["currency"] = currency
            return out

        outbound = _run_one_way(from_airport, to_airport, date, adults, children, seat, currency)
        inbound = _run_one_way(to_airport, from_airport, return_date, adults, children, seat, currency)
    except Exception as e:
        return {"error": str(e), "round_trip": True, "pairs": [], "count": 0, "currency": currency}

    out_items = outbound.get("itineraries", [])
    ret_items = inbound.get("itineraries", [])

    pairs = []
    used_return_idx = set()
    for o in out_items:
        o_airline = o.get("airlines", [None])[0]
        match_idx = None
        for i, r in enumerate(ret_items):
            if i in used_return_idx:
                continue
            if r.get("airlines", [None])[0] == o_airline:
                match_idx = i
                break
        if match_idx is not None:
            r = ret_items[match_idx]
            used_return_idx.add(match_idx)
            pairs.append(
                {
                    "airline": o_airline,
                    "total_price": o["price"] + r["price"],
                    "outbound": o,
                    "return": r,
                }
            )

    if not pairs and out_items and ret_items:
        o = min(out_items, key=lambda x: x["price"])
        r = min(ret_items, key=lambda x: x["price"])
        pairs.append(
            {
                "airline": None,
                "note": "no matching airline both ways - cheapest outbound + cheapest return combined",
                "total_price": o["price"] + r["price"],
                "outbound": o,
                "return": r,
            }
        )

    pairs.sort(key=lambda p: p["total_price"])
    return {
        "round_trip": True,
        "currency": currency,
        "pairs": pairs,
        "count": len(pairs),
        "outbound_options_seen": len(out_items),
        "return_options_seen": len(ret_items),
    }


@mcp.tool()
def health() -> dict:
    """Basic health check."""
    return {"ok": True}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

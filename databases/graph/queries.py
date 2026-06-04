"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

STUDENT TASK
------------
Design your graph schema (node labels, relationship types, properties)
based on the data in train-mock-data/, seed it with skeleton/seed_neo4j.py,
then implement the query_ functions below.

Functions prefixed with `query_` are called by the agent (skeleton/agent.py).
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a session, run Cypher, return data.

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]

# TODO: Implement the query_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """Find the fastest path between two stations, minimising total travel time."""
    rel_types = ""
    if network == "metro":
        rel_types = "METRO_LINK"
    elif network == "rail":
        rel_types = "RAIL_LINK"
    else:
        rel_types = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    cypher = """
        MATCH (start:Station {station_id: $origin_id})
        MATCH (end:Station {station_id: $destination_id})
        CALL apoc.algo.dijkstra(start, end, $rel_types, 'travel_time_min') YIELD path, weight
        RETURN [n in nodes(path) | {station_id: n.station_id, name: n.name}] AS stations,
               [r in relationships(path) | {
                   line: coalesce(r.line, 'Interchange'), 
                   time: r.travel_time_min, 
                   type: type(r)
               }] AS legs,
               weight AS total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id, rel_types=rel_types)
            record = result.single()
            if not record:
                return {"found": False}
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "path": record["stations"],
                "legs": record["legs"]
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """Find the cheapest path between two stations, minimising total estimated fare."""
    # We use a path match and REDUCE to calculate cross-property costs since weights are named differently.
    rel_types = ""
    if network == "metro":
        rel_types = "[:METRO_LINK*1..20]"
    elif network == "rail":
        rel_types = "[:RAIL_LINK*1..20]"
    else:
        rel_types = "[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..20]"
        
    fare_prop = "cost_first_usd" if fare_class == "first" else "cost_standard_usd"

    cypher = f"""
        MATCH p = (start:Station {{station_id: $origin_id}})-{rel_types}-(end:Station {{station_id: $destination_id}})
        WITH p, REDUCE(
            total = 0.0, r IN relationships(p) | 
            total + coalesce(r.cost_usd, 0.0) + coalesce(r[{fare_prop}], 0.0)
        ) AS total_fare
        ORDER BY total_fare ASC
        LIMIT 1
        RETURN [n in nodes(p) | {{station_id: n.station_id, name: n.name}}] AS stations,
               [r in relationships(p) | {{
                   line: coalesce(r.line, 'Interchange'), 
                   type: type(r)
               }}] AS legs,
               total_fare
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            if not record:
                return {"found": False}
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_fare_usd": record["total_fare"],
                "stations": record["stations"],
                "legs": record["legs"]
            }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """Find paths between two stations that avoid a specific intermediate station."""
    rel_types = ""
    if network == "metro":
        rel_types = "[:METRO_LINK*1..15]"
    elif network == "rail":
        rel_types = "[:RAIL_LINK*1..15]"
    else:
        rel_types = "[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]"
        
    cypher = f"""
        MATCH p = (start:Station {{station_id: $origin_id}})-{rel_types}-(end:Station {{station_id: $destination_id}})
        WHERE NONE(n IN nodes(p) WHERE n.station_id = $avoid_station_id)
        WITH p, reduce(t = 0, r IN relationships(p) | t + coalesce(r.travel_time_min, 0)) AS total_time
        ORDER BY total_time ASC
        LIMIT $max_routes
        RETURN [r in relationships(p) | {{
            line: coalesce(r.line, 'Interchange'),
            time: r.travel_time_min,
            type: type(r)
        }}] AS route_legs
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id, 
                                 avoid_station_id=avoid_station_id, max_routes=max_routes)
            return [record["route_legs"] for record in result]


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """Find a path crossing the network boundary via interchange relationships."""
    cypher = """
        MATCH p = (start:Station {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]-(end:Station {station_id: $destination_id})
        WHERE ANY(r IN relationships(p) WHERE type(r) = 'INTERCHANGE_TO')
        WITH p, reduce(t = 0, r IN relationships(p) | t + coalesce(r.travel_time_min, 0)) AS total_time
        ORDER BY total_time ASC
        LIMIT 1
        RETURN [n in nodes(p) | {station_id: n.station_id, name: n.name}] AS stations,
               total_time AS total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            if not record:
                return {"found": False}
            return {
                "found": True,
                "stations": record["stations"],
                "total_time_min": record["total_time_min"]
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """Find all stations within N hops of a delayed or disrupted station."""
    cypher = """
        MATCH p = (start:Station {station_id: $delayed_station_id})-[:METRO_LINK|RAIL_LINK*1..$hops]-(affected:Station)
        RETURN affected.station_id AS station_id, 
               affected.name AS name, 
               min(length(p)) AS hops_away,
               collect(distinct last(relationships(p)).line) AS lines_affected
        ORDER BY hops_away ASC
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, delayed_station_id=delayed_station_id, hops=hops)
            return [dict(record) for record in result]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """List all direct connections from a given station."""
    cypher = """
        MATCH (start:Station {station_id: $station_id})-[r]-(connected:Station)
        RETURN connected.station_id AS station_id,
               connected.name AS name,
               type(r) AS connection_type,
               r.line AS line,
               r.travel_time_min AS travel_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, station_id=station_id)
            return [dict(record) for record in result]
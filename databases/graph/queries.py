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


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path using Dijkstra algorithm by travel_time_min.
    Uses apoc.algo.dijkstra (requires APOC plugin).
    """
    with _driver() as driver:
        with driver.session() as session:
            # Infer network
            inferred_network = network
            if network == "auto":
                inferred_network = "metro" if origin_id.startswith("MS") else "national_rail"
            
            # Cypher with Dijkstra
            query = f"""
            MATCH (origin:Station {{station_id: $origin_id}})
            MATCH (dest:Station {{station_id: $dest_id}})
            
            CALL apoc.algo.dijkstra(
                origin,
                dest,
                'ROUTE',
                'travel_time_min'
            ) YIELD path, weight AS total_time_min
            
            // 如果指定單一網絡，過濾路徑
            {"WHERE all(rel IN relationships(path) WHERE rel.network = '" + inferred_network + "')" if inferred_network != "auto" else ""}
            
            WITH path, total_time_min,
                 [n IN nodes(path) | {{
                    station_id: n.station_id,
                    name: n.name,
                    network: n.network
                 }}] AS stations,
                 relationships(path) AS rels
            
            RETURN 
                true AS found,
                stations,
                total_time_min,
                [i IN range(0, size(rels)-1) | {{
                    from_station: stations[i].station_id,
                    to_station: stations[i+1].station_id,
                    travel_time_min: rels[i].travel_time_min,
                    line: rels[i].line
                }}] AS legs
            """
            
            try:
                result = session.run(query, origin_id=origin_id, dest_id=destination_id)
                record = result.single()
                
                if record:
                    return dict(record)
            except Exception as e:
                print(f"Dijkstra 查詢失敗: {e}")
            
            return {
                "found": False,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": None,
                "stations": [],
                "legs": []
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.
    Note: Graph stores travel_time; fare estimation requires PostgreSQL join.
    This version returns the route with fewest stops (proxy for lowest fare).

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_stops, stations (list), legs, estimated_fare_usd
    """
    with _driver() as driver:
        with driver.session() as session:
            # Infer network from station IDs if network="auto"
            inferred_network = network
            if network == "auto":
                if origin_id.startswith("MS"):
                    inferred_network = "metro"
                elif origin_id.startswith("NR"):
                    inferred_network = "national_rail"
            
            # Find path with fewest hops (least stops = potentially cheapest)
            network_filter = ""
            if inferred_network in ("metro", "national_rail"):
                network_filter = f" AND all(rel IN relationships(path) WHERE rel.network = '{inferred_network}')"
            
            query = f"""
            MATCH (origin:Station {{station_id: $origin_id}})
            MATCH (dest:Station {{station_id: $dest_id}})
            MATCH path = shortestPath((origin)-[r:ROUTE*]->(dest))
            WHERE true{network_filter}
            WITH path,
                 [n IN nodes(path) | {{station_id: n.station_id, name: n.name, network: n.network}}] AS stations,
                 size(relationships(path)) AS num_legs,
                 relationships(path) AS rels
            RETURN 
                true AS found,
                length(stations) AS total_stops,
                stations,
                [i IN range(0, size(rels)-1) | {{
                    from_station: stations[i].station_id,
                    to_station: stations[i+1].station_id,
                    travel_time_min: rels[i].travel_time_min,
                    line: rels[i].line
                }}] AS legs,
                'Estimated via PostgreSQL' AS estimated_fare_usd
            """
            
            result = session.run(query, origin_id=origin_id, dest_id=destination_id)
            record = result.single()
            
            if record:
                return dict(record)
            else:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "total_stops": None,
                    "stations": [],
                    "legs": [],
                    "estimated_fare_usd": None
                }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find paths between two stations that avoid a specific intermediate station.
    Useful for routing around a delayed or closed station.

    Args:
        origin_id:         e.g. "NR01"
        destination_id:    e.g. "NR05"
        avoid_station_id:  e.g. "NR03"
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return

    Returns:
        List of routes, each route is a list of leg dicts.
        Each leg has: from_station, to_station, travel_time_min, line
    """
    with _driver() as driver:
        with driver.session() as session:
            # Infer network from station IDs if network="auto"
            inferred_network = network
            if network == "auto":
                if origin_id.startswith("MS"):
                    inferred_network = "metro"
                elif origin_id.startswith("NR"):
                    inferred_network = "national_rail"
            
            # Find paths avoiding the given station
            network_filter = ""
            if inferred_network in ("metro", "national_rail"):
                network_filter = f" AND all(rel IN relationships(path) WHERE rel.network = '{inferred_network}')"
            
            query = f"""
            MATCH (origin:Station {{station_id: $origin_id}})
            MATCH (dest:Station {{station_id: $dest_id}})
            MATCH (avoid:Station {{station_id: $avoid_id}})
            MATCH path = (origin)-[r:ROUTE*]-(dest)
            WHERE NOT any(node IN nodes(path) WHERE node.station_id = $avoid_id){network_filter}
            WITH path,
                 [n IN nodes(path) | {{station_id: n.station_id, name: n.name}}] AS stations,
                 relationships(path) AS rels
            RETURN 
                [i IN range(0, size(rels)-1) | {{
                    from_station: stations[i].station_id,
                    to_station: stations[i+1].station_id,
                    travel_time_min: rels[i].travel_time_min,
                    line: rels[i].line
                }}] AS leg
            LIMIT $max_routes
            """
            
            result = session.run(
                query,
                origin_id=origin_id,
                dest_id=destination_id,
                avoid_id=avoid_station_id,
                max_routes=max_routes
            )
            
            routes = []
            for record in result:
                routes.append(record["leg"])
            
            return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via interchange relationships.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, origin_station, destination_station, 
               interchange_points (list), total_time_min, stations (list)
    """
    with _driver() as driver:
        with driver.session() as session:
            # Find path that uses INTERCHANGE relationship
            # Pattern: (origin)-[:ROUTE*]->(interchange1)-[:INTERCHANGE]-(interchange2)-[:ROUTE*]->(dest)
            query = """
            MATCH (origin:Station {station_id: $origin_id})
            MATCH (dest:Station {station_id: $dest_id})
            
            // Pattern 1: ROUTE from origin to any interchange point
            MATCH path1 = (origin)-[:ROUTE*]-(interchange1:Station)
            
            // Then INTERCHANGE to another network
            MATCH (interchange1)-[:INTERCHANGE]-(interchange2:Station)
            
            // Then ROUTE from interchange2 to destination
            MATCH path2 = (interchange2)-[:ROUTE*]-(dest)
            
            WITH 
                [n IN nodes(path1) | {station_id: n.station_id, name: n.name, network: n.network}] AS path1_stations,
                [n IN nodes(path2) | {station_id: n.station_id, name: n.name, network: n.network}] AS path2_stations,
                interchange1.station_id as interchange1_id,
                interchange1.name as interchange1_name,
                interchange2.station_id as interchange2_id,
                interchange2.name as interchange2_name,
                reduce(t = 0, rel IN relationships(path1) | t + rel.travel_time_min) as time1,
                reduce(t = 0, rel IN relationships(path2) | t + rel.travel_time_min) as time2
            
            RETURN 
                true AS found,
                origin.station_id AS origin_station,
                dest.station_id AS destination_station,
                [{station_id: interchange1_id, name: interchange1_name}, 
                 {station_id: interchange2_id, name: interchange2_name}] AS interchange_points,
                (time1 + time2) AS total_time_min,
                path1_stations + path2_stations[1..] AS stations
            LIMIT 1
            """
            
            result = session.run(query, origin_id=origin_id, dest_id=destination_id)
            record = result.single()
            
            if record:
                return dict(record)
            else:
                return {
                    "found": False,
                    "origin_station": origin_id,
                    "destination_station": destination_id,
                    "interchange_points": [],
                    "total_time_min": None,
                    "stations": []
                }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    Works on both metro and national rail networks.

    Args:
        delayed_station_id: e.g. "NR03" or "MS01"
        hops:               how many connections out to search (default 2)

    Returns:
        List of dicts: {station_id, name, network, hops_away, lines_affected}
    """
    with _driver() as driver:
        with driver.session() as session:
            query = """
            MATCH (delayed:Station {station_id: $delayed_id})
            MATCH (affected:Station)
            WHERE affected.station_id <> delayed.station_id
            
            MATCH path = shortestPath((delayed)-[:ROUTE*..{hops}]-(affected))
            
            WITH 
                affected.station_id as station_id,
                affected.name as name,
                affected.network as network,
                affected.lines as lines,
                length(path) as hops_away
            
            RETURN 
                station_id,
                name,
                network,
                hops_away,
                lines
            ORDER BY hops_away ASC
            """
            
            result = session.run(query, delayed_id=delayed_station_id, hops=hops)
            
            ripple = []
            for record in result:
                ripple.append({
                    "station_id": record["station_id"],
                    "name": record["name"],
                    "network": record["network"],
                    "hops_away": record["hops_away"],
                    "lines_affected": record["lines"]
                })
            
            return ripple


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.

    Args:
        station_id: e.g. "MS01" or "NR01"
    
    Returns:
        List of dicts with: {station_id, name, network, line, travel_time_min, direction}
    """
    with _driver() as driver:
        with driver.session() as session:
            query = """
            MATCH (s:Station {station_id: $station_id})
            MATCH (s)-[r:ROUTE]->(adjacent:Station)
            
            RETURN 
                adjacent.station_id as station_id,
                adjacent.name as name,
                adjacent.network as network,
                r.line as line,
                r.travel_time_min as travel_time_min,
                'outbound' as direction
            
            UNION
            
            MATCH (s:Station {station_id: $station_id})
            MATCH (s)<-[r:ROUTE]-(adjacent:Station)
            
            RETURN 
                adjacent.station_id as station_id,
                adjacent.name as name,
                adjacent.network as network,
                r.line as line,
                r.travel_time_min as travel_time_min,
                'inbound' as direction
            
            ORDER BY line ASC, travel_time_min ASC
            """
            
            result = session.run(query, station_id=station_id)
            
            connections = []
            for record in result:
                connections.append({
                    "station_id": record["station_id"],
                    "name": record["name"],
                    "network": record["network"],
                    "line": record["line"],
                    "travel_time_min": record["travel_time_min"],
                    "direction": record["direction"]
                })
            
            return connections

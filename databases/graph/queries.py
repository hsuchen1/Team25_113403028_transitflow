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
    Find the fastest path between two stations, minimising total travel time.
    Uses apoc.algo.dijkstra (APOC required; enabled in docker-compose.yml).

    Args:
        origin_id:       e.g. "MS01" or "NR01"
        destination_id:  e.g. "MS09" or "NR05"
        network:         "metro", "rail", or "auto" (inferred from IDs)

    Returns:
        dict with keys: found, origin_id, destination_id,
                        total_time_min, path (list of station dicts), legs
    """
    # Handle same station case
    if origin_id == destination_id:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Origin and destination are the same"
        }
    
    # Infer network from IDs if "auto"
    if network == "auto":
        origin_is_metro = origin_id.startswith("MS")
        dest_is_metro = destination_id.startswith("MS")
        
        if origin_is_metro != dest_is_metro:
            return {
                "found": False,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "reason": "Origin and destination are on different networks"
            }
        
        network = "metro" if origin_is_metro else "rail"
    
    # Determine the node label based on network
    node_label = "MetroStation" if network == "metro" else "NationalRailStation"
    
    with _driver() as driver:
        with driver.session() as session:
            # Execute Dijkstra algorithm
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $origin_id}})
            MATCH (end:{node_label} {{station_id: $destination_id}})
            CALL apoc.algo.dijkstra(start, end, 'CONNECTS_TO_ON_LINE>', 'travel_time_min')
            YIELD path, weight
            RETURN path, weight
            """
            
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            
            if not record:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "reason": "No path found between stations"
                }
            
            path = record["path"]
            total_time_min = record["weight"]
            
            # Extract nodes from path
            nodes = path.nodes
            relationships = path.relationships
            
            # Build path list (station dicts)
            path_list = []
            for node in nodes:
                path_list.append({
                    "station_id": node["station_id"],
                    "name": node["name"],
                    "lines": node["lines"]
                })
            
            # Build legs list (individual connections with travel times)
            legs = []
            for rel in relationships:
                legs.append({
                    "from": rel.start_node["station_id"],
                    "to": rel.end_node["station_id"],
                    "line": rel["line"],
                    "travel_time_min": rel["travel_time_min"]
                })
            
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": total_time_min,
                "path": path_list,
                "legs": legs
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

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd (approximate), stations, legs
    """
    # Handle same station case
    if origin_id == destination_id:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Origin and destination are the same"
        }
    
    # Infer network from IDs if "auto"
    if network == "auto":
        origin_is_metro = origin_id.startswith("MS")
        dest_is_metro = destination_id.startswith("MS")
        
        if origin_is_metro != dest_is_metro:
            return {
                "found": False,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "reason": "Origin and destination are on different networks"
            }
        
        network = "metro" if origin_is_metro else "rail"
    
    # Determine the node label and cost property based on network and fare_class
    node_label = "MetroStation" if network == "metro" else "NationalRailStation"
    cost_property = "cost_usd" if network == "metro" else f"cost_{fare_class}_usd"
    
    with _driver() as driver:
        with driver.session() as session:
            # Execute Dijkstra algorithm with cost property
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $origin_id}})
            MATCH (end:{node_label} {{station_id: $destination_id}})
            CALL apoc.algo.dijkstra(start, end, 'CONNECTS_TO_ON_LINE>', '{cost_property}')
            YIELD path, weight
            RETURN path, weight
            """
            
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            
            if not record:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "reason": "No path found between stations"
                }
            
            path = record["path"]
            total_fare_usd = record["weight"]
            
            # Extract nodes from path
            nodes = path.nodes
            relationships = path.relationships
            
            # Build stations list (station dicts)
            stations = []
            for node in nodes:
                stations.append({
                    "station_id": node["station_id"],
                    "name": node["name"],
                    "lines": node["lines"]
                })
            
            # Build legs list (individual connections with fares)
            legs = []
            for rel in relationships:
                if network == "metro":
                    cost = rel["cost_usd"]
                else:
                    cost = rel[cost_property]
                
                legs.append({
                    "from": rel.start_node["station_id"],
                    "to": rel.end_node["station_id"],
                    "line": rel["line"],
                    f"cost_{fare_class}_usd": cost
                })
            
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_fare_usd": total_fare_usd,
                "fare_class": fare_class if network == "rail" else "standard",
                "stations": stations,
                "legs": legs
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
        avoid_station_id:  e.g. "NR03" (station to avoid in the path)
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return

    Returns:
        List of routes, each route is a list of leg dicts with distinct paths
    """
    # Handle invalid cases
    if origin_id == destination_id:
        return []
    
    if origin_id == avoid_station_id or destination_id == avoid_station_id:
        return []  # Cannot avoid origin or destination
    
    # Infer network from IDs if "auto"
    if network == "auto":
        origin_is_metro = origin_id.startswith("MS")
        dest_is_metro = destination_id.startswith("MS")
        
        if origin_is_metro != dest_is_metro:
            return []  # Different networks
        
        network = "metro" if origin_is_metro else "rail"
    
    # Determine the node label based on network
    node_label = "MetroStation" if network == "metro" else "NationalRailStation"
    
    alternative_routes = []
    found_station_sequences = set()  # Track complete paths to avoid duplicates
    
    with _driver() as driver:
        for route_num in range(max_routes):
            with driver.session() as session:
                # Get all simple paths
                cypher = f"""
                MATCH (start:{node_label} {{station_id: $origin_id}})
                MATCH (end:{node_label} {{station_id: $destination_id}})
                CALL apoc.algo.allSimplePaths(start, end, 'CONNECTS_TO_ON_LINE>', 20)
                YIELD path
                RETURN path
                LIMIT 100
                """
                
                result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
                records = list(result)
                
                if not records:
                    break
                
                # Find first valid path that:
                # 1. Does not contain avoid_station_id
                # 2. Has not been found before
                valid_path = None
                for record in records:
                    path = record["path"]
                    nodes = path.nodes
                    
                    # Get station sequence for this path
                    station_sequence = tuple(node["station_id"] for node in nodes)
                    
                    # Check if path contains avoid_station
                    if avoid_station_id in station_sequence:
                        continue
                    
                    # Check if we've already found this exact sequence
                    if station_sequence in found_station_sequences:
                        continue
                    
                    # Found valid path
                    valid_path = (path, station_sequence)
                    break
                
                if not valid_path:
                    break
                
                path, station_sequence = valid_path
                relationships = path.relationships
                
                # Mark this sequence as found
                found_station_sequences.add(station_sequence)
                
                # Build legs for this route
                legs = []
                for rel in relationships:
                    from_id = rel.start_node["station_id"]
                    to_id = rel.end_node["station_id"]
                    legs.append({
                        "from": from_id,
                        "to": to_id,
                        "line": rel["line"],
                        "travel_time_min": rel["travel_time_min"]
                    })
                
                alternative_routes.append(legs)
    
    return alternative_routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via interchange relationships.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, origin_id, destination_id, total_time_min,
               interchange_point, path (list of stations), legs
    """
    # Determine networks based on IDs
    origin_is_metro = origin_id.startswith("MS")
    dest_is_metro = destination_id.startswith("MS")
    
    # Must be cross-network
    if origin_is_metro == dest_is_metro:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Origin and destination are on the same network"
        }
    
    # Same station check
    if origin_id == destination_id:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Origin and destination are the same"
        }
    
    with _driver() as driver:
        with driver.session() as session:
            # Build Cypher query based on network direction
            if origin_is_metro:
                # Metro -> Rail
                cypher = """
                MATCH (start:MetroStation {station_id: $origin_id})
                MATCH (end:NationalRailStation {station_id: $destination_id})
                CALL apoc.algo.allSimplePaths(start, end, 'CONNECTS_TO_ON_LINE>|INTERCHANGE_WITH>', 20)
                YIELD path
                WHERE size([r IN relationships(path) WHERE type(r) = 'INTERCHANGE_WITH']) = 1
                RETURN path
                LIMIT 1
                """
            else:
                # Rail -> Metro
                cypher = """
                MATCH (start:NationalRailStation {station_id: $origin_id})
                MATCH (end:MetroStation {station_id: $destination_id})
                CALL apoc.algo.allSimplePaths(start, end, 'CONNECTS_TO_ON_LINE>|INTERCHANGE_WITH>', 20)
                YIELD path
                WHERE size([r IN relationships(path) WHERE type(r) = 'INTERCHANGE_WITH']) = 1
                RETURN path
                LIMIT 1
                """
            
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            
            if not record:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "reason": "No interchange path found between networks"
                }
            
            path = record["path"]
            nodes = path.nodes
            relationships = path.relationships
            
            # Build path list (all stations)
            path_list = []
            for node in nodes:
                path_list.append({
                    "station_id": node["station_id"],
                    "name": node["name"],
                    "lines": node["lines"]
                })
            
            # Find interchange point (the INTERCHANGE_WITH relationship)
            interchange_point = None
            for rel in relationships:
                if rel.type == "INTERCHANGE_WITH":
                    interchange_point = f"{rel.start_node['station_id']} ↔ {rel.end_node['station_id']}"
                    break
            
            # Build legs list with total time calculation
            legs = []
            total_time_min = 0
            for rel in relationships:
                leg_time = rel.get("travel_time_min", 0)
                total_time_min += leg_time
                
                # INTERCHANGE_WITH has 0 travel time, CONNECTS_TO_ON_LINE has travel_time_min
                legs.append({
                    "from": rel.start_node["station_id"],
                    "to": rel.end_node["station_id"],
                    "line": rel.get("line", "INTERCHANGE"),
                    "travel_time_min": leg_time
                })
            
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": total_time_min,
                "interchange_point": interchange_point,
                "path": path_list,
                "legs": legs
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
        List of dicts: {station_id, name, hops_away, lines_affected}
    """
    if not delayed_station_id or hops < 1:
        return []
    
    # Determine network from ID
    is_metro = delayed_station_id.startswith("MS")
    node_label = "MetroStation" if is_metro else "NationalRailStation"
    
    affected_stations = {}  # {station_id: {name, hops_away, lines_affected}}
    
    with _driver() as driver:
        with driver.session() as session:
            # Get all paths within N hops from the delayed station
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $delayed_station_id}})
            CALL apoc.path.expandConfig(start, {{
                relationshipFilter: 'CONNECTS_TO_ON_LINE>|INTERCHANGE_WITH>',
                minLevel: 1,
                maxLevel: {hops}
            }})
            YIELD path
            RETURN path
            """
            
            result = session.run(cypher, delayed_station_id=delayed_station_id)
            records = list(result)
            
            for record in records:
                path = record["path"]
                nodes = path.nodes
                relationships = path.relationships
                
                # The last node in each path is an affected station
                if len(nodes) > 1:
                    affected_node = nodes[-1]
                    station_id = affected_node["station_id"]
                    hops_away = len(nodes) - 1  # Number of edges from start
                    
                    # Extract lines from the relationships in this path
                    lines = set()
                    for rel in relationships:
                        if rel.get("line"):
                            lines.add(rel["line"])
                    
                    # Keep only the shortest path to each station
                    if station_id not in affected_stations or hops_away < affected_stations[station_id]["hops_away"]:
                        affected_stations[station_id] = {
                            "name": affected_node.get("name", "Unknown"),
                            "hops_away": hops_away,
                            "lines_affected": sorted(list(lines))
                        }
    
    # Convert to list of dicts sorted by hops_away, then by station_id
    result_list = [
        {
            "station_id": station_id,
            "name": data["name"],
            "hops_away": data["hops_away"],
            "lines_affected": data["lines_affected"]
        }
        for station_id, data in affected_stations.items()
    ]
    
    result_list.sort(key=lambda x: (x["hops_away"], x["station_id"]))
    return result_list


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.

    Args:
        station_id: e.g. "MS01" or "NR01"

    Returns:
        List of dicts: {neighbor_id, neighbor_name, relationship_type, 
                        line, travel_time_min, cost_usd}
        Sorted by travel_time_min (ascending)
    """
    if not station_id:
        return []
    
    # Determine network from ID
    is_metro = station_id.startswith("MS")
    node_label = "MetroStation" if is_metro else "NationalRailStation"
    
    connections = []
    
    with _driver() as driver:
        with driver.session() as session:
            # Query all direct connections (both CONNECTS_TO_ON_LINE and INTERCHANGE_WITH)
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $station_id}})
            -[rel:CONNECTS_TO_ON_LINE|INTERCHANGE_WITH]->
            (neighbor)
            RETURN neighbor, rel, type(rel) AS relationship_type
            """
            
            result = session.run(cypher, station_id=station_id)
            records = list(result)
            
            if not records:
                return []
            
            # Process each connection
            for record in records:
                neighbor = record["neighbor"]
                rel = record["rel"]
                relationship_type = record["relationship_type"]
                
                # Extract relevant properties based on relationship type
                if relationship_type == "CONNECTS_TO_ON_LINE":
                    connection_dict = {
                        "neighbor_id": neighbor["station_id"],
                        "neighbor_name": neighbor.get("name", "Unknown"),
                        "relationship_type": relationship_type,
                        "line": rel.get("line"),
                        "travel_time_min": rel.get("travel_time_min", 0),
                        "cost_usd": rel.get("cost_usd")
                    }
                else:  # INTERCHANGE_WITH
                    connection_dict = {
                        "neighbor_id": neighbor["station_id"],
                        "neighbor_name": neighbor.get("name", "Unknown"),
                        "relationship_type": relationship_type,
                        "line": None,
                        "travel_time_min": 0,
                        "cost_usd": None
                    }
                
                connections.append(connection_dict)
    
    # Sort by travel_time_min (ascending)
    connections.sort(key=lambda x: (x["travel_time_min"], x["neighbor_id"]))
    
    return connections

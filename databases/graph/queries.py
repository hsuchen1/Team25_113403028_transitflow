"""
TransitFlow — Neo4j Graph Database Layer (Updated for Custom Schema)
=========================================
This module handles all queries to Neo4j, fully compatible with the new seed_neo4j.py.

UPDATED GRAPH MAPPING:
  - Metro connections: METRO_LINK (uses r.cost_usd)
  - Rail connections:  RAIL_LINK (uses r.cost_standard_usd or r.cost_first_usd)
  - Interchanges:      INTERCHANGE_TO (bidirectional, travel_time_min: 5)
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────

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
    Supports both same-network (METRO_LINK or RAIL_LINK) and cross-network (INTERCHANGE_TO).
    """
    if origin_id == destination_id:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Origin and destination are the same"
        }
    
    origin_is_metro = origin_id.startswith("MS")
    dest_is_metro = destination_id.startswith("MS")
    is_cross_network = origin_is_metro != dest_is_metro
    
    if network == "auto" and not is_cross_network:
        network = "metro" if origin_is_metro else "rail"
    
    with _driver() as driver:
        with driver.session() as session:
            if is_cross_network:
                # Cross-network: Allow all relevant relationship types
                cypher = """
                MATCH (start {station_id: $origin_id})
                MATCH (end {station_id: $destination_id})
                CALL apoc.algo.allSimplePaths(start, end, 'METRO_LINK>|RAIL_LINK>|INTERCHANGE_TO>', 20)
                YIELD path
                RETURN path, reduce(total=0, rel IN relationships(path) | total + rel.travel_time_min) as weight
                ORDER BY weight ASC
                LIMIT 1
                """
            else:
                # Same-network: Filter strictly by network type to use correct relationship
                node_label = "MetroStation" if origin_is_metro else "NationalRailStation"
                rel_type = "METRO_LINK" if origin_is_metro else "RAIL_LINK"
                cypher = f"""
                MATCH (start:{node_label} {{station_id: $origin_id}})
                MATCH (end:{node_label} {{station_id: $destination_id}})
                CALL apoc.algo.dijkstra(start, end, '{rel_type}>', 'travel_time_min')
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
            
            path_list = []
            for node in path.nodes:
                path_list.append({
                    "station_id": node["station_id"],
                    "name": node["name"],
                    "lines": node["lines"]
                })
            
            legs = []
            for rel in path.relationships:
                line = rel.get("line", "INTERCHANGE")  # INTERCHANGE_TO has no line property
                legs.append({
                    "from": rel.start_node["station_id"],
                    "to": rel.end_node["station_id"],
                    "line": line,
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
    """
    if origin_id == destination_id:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Origin and destination are the same"
        }
    
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
    
    node_label = "MetroStation" if network == "metro" else "NationalRailStation"
    rel_type = "METRO_LINK" if network == "metro" else "RAIL_LINK"
    cost_property = "cost_usd" if network == "metro" else f"cost_{fare_class}_usd"
    
    with _driver() as driver:
        with driver.session() as session:
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $origin_id}})
            MATCH (end:{node_label} {{station_id: $destination_id}})
            CALL apoc.algo.dijkstra(start, end, '{rel_type}>', '{cost_property}')
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
            
            stations = []
            for node in path.nodes:
                stations.append({
                    "station_id": node["station_id"],
                    "name": node["name"],
                    "lines": node["lines"]
                })
            
            legs = []
            for rel in path.relationships:
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
    """
    if origin_id == destination_id or origin_id == avoid_station_id or destination_id == avoid_station_id:
        return []
    
    if network == "auto":
        origin_is_metro = origin_id.startswith("MS")
        dest_is_metro = destination_id.startswith("MS")
        if origin_is_metro != dest_is_metro:
            return []
        network = "metro" if origin_is_metro else "rail"
    
    node_label = "MetroStation" if network == "metro" else "NationalRailStation"
    rel_type = "METRO_LINK" if network == "metro" else "RAIL_LINK"
    
    alternative_routes = []
    found_station_sequences = set()
    
    with _driver() as driver:
        for route_num in range(max_routes):
            with driver.session() as session:
                cypher = f"""
                MATCH (start:{node_label} {{station_id: $origin_id}})
                MATCH (end:{node_label} {{station_id: $destination_id}})
                CALL apoc.algo.allSimplePaths(start, end, '{rel_type}>', 20)
                YIELD path
                RETURN path
                LIMIT 100
                """
                
                result = session.run(cypher, origin_id=origin_id, destination_id=destination_id)
                records = list(result)
                
                if not records:
                    break
                
                valid_path = None
                for record in records:
                    path = record["path"]
                    station_sequence = tuple(node["station_id"] for node in path.nodes)
                    
                    if avoid_station_id in station_sequence or station_sequence in found_station_sequences:
                        continue
                    
                    valid_path = (path, station_sequence)
                    break
                
                if not valid_path:
                    break
                
                path, station_sequence = valid_path
                found_station_sequences.add(station_sequence)
                
                legs = []
                for rel in path.relationships:
                    legs.append({
                        "from": rel.start_node["station_id"],
                        "to": rel.end_node["station_id"],
                        "line": rel["line"],
                        "travel_time_min": rel["travel_time_min"]
                    })
                
                alternative_routes.append(legs)
    
    return alternative_routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find the fastest path between a metro station and a national rail station via INTERCHANGE_TO.
    """
    origin_is_metro = origin_id.startswith("MS")
    dest_is_metro = destination_id.startswith("MS")
    
    if origin_is_metro == dest_is_metro or origin_id == destination_id:
        return {
            "found": False,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "reason": "Invalid cross-network routing parameters"
        }
    
    # Target label settings based on direction
    start_label = "MetroStation" if origin_is_metro else "NationalRailStation"
    end_label = "NationalRailStation" if origin_is_metro else "MetroStation"
    
    with _driver() as driver:
        with driver.session() as session:
            cypher = f"""
            MATCH (start:{start_label} {{station_id: $origin_id}})
            MATCH (end:{end_label} {{station_id: $destination_id}})
            CALL apoc.algo.allSimplePaths(start, end, 'METRO_LINK>|RAIL_LINK>|INTERCHANGE_TO>', 20)
            YIELD path
            WHERE size([r IN relationships(path) WHERE type(r) = 'INTERCHANGE_TO']) = 1
            RETURN path, reduce(total=0, rel IN relationships(path) | total + rel.travel_time_min) as weight
            ORDER BY weight ASC
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
            
            path_list = []
            for node in path.nodes:
                path_list.append({
                    "station_id": node["station_id"],
                    "name": node["name"],
                    "lines": node["lines"]
                })
            
            interchange_point = None
            for rel in path.relationships:
                if rel.type == "INTERCHANGE_TO":
                    interchange_point = f"{rel.start_node['station_id']} ↔ {rel.end_node['station_id']}"
                    break
            
            legs = []
            total_time_min = 0
            for rel in path.relationships:
                leg_time = rel.get("travel_time_min", 0)
                total_time_min += leg_time
                
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
    Find all stations within N hops of a delayed station using new relationship types.
    """
    if not delayed_station_id or hops < 1:
        return []
    
    is_metro = delayed_station_id.startswith("MS")
    node_label = "MetroStation" if is_metro else "NationalRailStation"
    affected_stations = {}
    
    with _driver() as driver:
        with driver.session() as session:
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $delayed_station_id}})
            CALL apoc.path.expandConfig(start, {{
                relationshipFilter: 'METRO_LINK>|RAIL_LINK>|INTERCHANGE_TO>',
                minLevel: 1,
                maxLevel: {hops}
            }})
            YIELD path
            RETURN path
            """
            
            result = session.run(cypher, delayed_station_id=delayed_station_id)
            
            for record in result:
                path = record["path"]
                nodes = path.nodes
                relationships = path.relationships
                
                if len(nodes) > 1:
                    affected_node = nodes[-1]
                    station_id = affected_node["station_id"]
                    hops_away = len(nodes) - 1
                    
                    lines = set()
                    for rel in relationships:
                        if rel.get("line"):
                            lines.add(rel["line"])
                    
                    if station_id not in affected_stations or hops_away < affected_stations[station_id]["hops_away"]:
                        affected_stations[station_id] = {
                            "name": affected_node.get("name", "Unknown"),
                            "hops_away": hops_away,
                            "lines_affected": sorted(list(lines))
                        }
    
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


# ── TASK 6: STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.
    """
    if not station_id:
        return []
    
    is_metro = station_id.startswith("MS")
    node_label = "MetroStation" if is_metro else "NationalRailStation"
    connections = []
    
    with _driver() as driver:
        with driver.session() as session:
            cypher = f"""
            MATCH (start:{node_label} {{station_id: $station_id}})
            -[rel:METRO_LINK|RAIL_LINK|INTERCHANGE_TO]->
            (neighbor)
            RETURN neighbor, rel, type(rel) AS relationship_type
            """
            
            result = session.run(cypher, station_id=station_id)
            
            for record in result:
                neighbor = record["neighbor"]
                rel = record["rel"]
                relationship_type = record["relationship_type"]
                
                if relationship_type in ("METRO_LINK", "RAIL_LINK"):
                    connection_dict = {
                        "neighbor_id": neighbor["station_id"],
                        "neighbor_name": neighbor.get("name", "Unknown"),
                        "relationship_type": relationship_type,
                        "line": rel.get("line"),
                        "travel_time_min": rel.get("travel_time_min", 0),
                        "cost_usd": rel.get("cost_usd") if relationship_type == "METRO_LINK" else rel.get("cost_standard_usd")
                    }
                else:  # INTERCHANGE_TO
                    connection_dict = {
                        "neighbor_id": neighbor["station_id"],
                        "neighbor_name": neighbor.get("name", "Unknown"),
                        "relationship_type": relationship_type,
                        "line": None,
                        "travel_time_min": rel.get("travel_time_min", 5),
                        "cost_usd": None
                    }
                connections.append(connection_dict)
    
    connections.sort(key=lambda x: (x["travel_time_min"], x["neighbor_id"]))
    return connections


# ── TASK 6 EXTENSION: ALL PATHS BETWEEN ───────────────────────────────────────

def query_all_paths_between(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    # limit: int = 5,
) -> list[dict]:
    """
    Find all possible paths between two stations, sorted by travel time.
    """
    limit = 5  # For now, we can keep a default limit to prevent excessive results
    if origin_id == destination_id:
        return []
    
    origin_is_metro = origin_id.startswith("MS")
    dest_is_metro = destination_id.startswith("MS")
    is_cross_network = origin_is_metro != dest_is_metro
    
    if network == "auto" and not is_cross_network:
        network = "metro" if origin_is_metro else "rail"
    
    paths_data = []
    
    with _driver() as driver:
        with driver.session() as session:
            if is_cross_network:
                cypher = """
                MATCH (start {station_id: $origin_id})
                MATCH (end {station_id: $destination_id})
                CALL apoc.algo.allSimplePaths(start, end, 'METRO_LINK>|RAIL_LINK>|INTERCHANGE_TO>', 20)
                YIELD path
                RETURN path, reduce(total=0, rel IN relationships(path) | total + rel.travel_time_min) as total_time
                ORDER BY total_time ASC
                LIMIT $limit
                """
            else:
                node_label = "MetroStation" if origin_is_metro else "NationalRailStation"
                rel_type = "METRO_LINK" if origin_is_metro else "RAIL_LINK"
                cypher = f"""
                MATCH (start:{node_label} {{station_id: $origin_id}})
                MATCH (end:{node_label} {{station_id: $destination_id}})
                CALL apoc.algo.allSimplePaths(start, end, '{rel_type}>', 20)
                YIELD path
                RETURN path, reduce(total=0, rel IN relationships(path) | total + rel.travel_time_min) as total_time
                ORDER BY total_time ASC
                LIMIT $limit
                """
            
            result = session.run(cypher, origin_id=origin_id, destination_id=destination_id, limit=limit)
            
            for path_index, record in enumerate(result, 1):
                path = record["path"]
                total_time_min = record["total_time"]
                relationships = path.relationships
                
                num_stops = len(path.nodes)
                num_transfers = sum(1 for rel in relationships if rel.type == "INTERCHANGE_TO")
                
                transfer_points = []
                for rel in relationships:
                    if rel.type == "INTERCHANGE_TO":
                        transfer_points.append({
                            "from": rel.start_node["station_id"],
                            "to": rel.end_node["station_id"],
                            "type": "network_interchange"
                        })
                
                for i in range(len(relationships) - 1):
                    current_rel = relationships[i]
                    next_rel = relationships[i + 1]
                    
                    if current_rel.type in ("METRO_LINK", "RAIL_LINK") and next_rel.type in ("METRO_LINK", "RAIL_LINK"):
                        current_line = current_rel.get("line")
                        next_line = next_rel.get("line")
                        
                        if current_line and next_line and current_line != next_line:
                            transfer_station = current_rel.end_node["station_id"]
                            if not any(t.get("from") == transfer_station or t.get("to") == transfer_station for t in transfer_points):
                                transfer_points.append({
                                    "station_id": transfer_station,
                                    "type": "line_change",
                                    "from_line": current_line,
                                    "to_line": next_line
                                })
                
                stations = []
                for node in path.nodes:
                    stations.append({
                        "station_id": node["station_id"],
                        "name": node.get("name", "Unknown"),
                        "lines": node.get("lines", [])
                    })
                
                legs = []
                for rel in relationships:
                    legs.append({
                        "from": rel.start_node["station_id"],
                        "to": rel.end_node["station_id"],
                        "line": rel.get("line", "INTERCHANGE"),
                        "travel_time_min": rel.get("travel_time_min", 0)
                    })
                
                paths_data.append({
                    "path_index": path_index,
                    "num_stops": num_stops,
                    "num_transfers": num_transfers,
                    "total_time_min": total_time_min,
                    "transfer_points": transfer_points,
                    "stations": stations,
                    "legs": legs
                })
    
    return paths_data
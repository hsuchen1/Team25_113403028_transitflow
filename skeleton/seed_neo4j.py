"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Design your graph schema (node labels, relationship types, properties)
based on the data in these files, then implement the seed() function below.
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")
    metro_schedules = _load("metro_schedules.json")
    rail_schedules = _load("national_rail_schedules.json")

    # Build cost mappings for metro (from_station, to_station) -> cost_usd
    metro_costs = {}
    for schedule in metro_schedules:
        stops = schedule["stops_in_order"]
        base_fare = schedule["base_fare_usd"]
        per_stop_rate = schedule["per_stop_rate_usd"]
        for i in range(len(stops)):
            for j in range(i + 1, len(stops)):
                from_station = stops[i]
                to_station = stops[j]
                stops_travelled = j - i
                cost = base_fare + (stops_travelled - 1) * per_stop_rate
                # Store the minimum cost if route exists via multiple schedules
                key = (from_station, to_station)
                if key not in metro_costs or cost < metro_costs[key]:
                    metro_costs[key] = cost

    # Build cost mappings for national rail (from_station, to_station) -> {standard, first}
    rail_costs = {}  # (from_station, to_station) -> {"standard": cost, "first": cost}
    for schedule in rail_schedules:
        stops = schedule["stops_in_order"]
        fare_classes = schedule["fare_classes"]
        for i in range(len(stops)):
            for j in range(i + 1, len(stops)):
                from_station = stops[i]
                to_station = stops[j]
                stops_travelled = j - i
                # Calculate cost for each fare class
                standard_cost = fare_classes["standard"]["base_fare_usd"] + (stops_travelled - 1) * fare_classes["standard"]["per_stop_rate_usd"]
                first_cost = fare_classes["first"]["base_fare_usd"] + (stops_travelled - 1) * fare_classes["first"]["per_stop_rate_usd"]
                key = (from_station, to_station)
                if key not in rail_costs:
                    rail_costs[key] = {"standard": standard_cost, "first": first_cost}
                else:
                    # Store minimum cost for each class
                    rail_costs[key]["standard"] = min(rail_costs[key]["standard"], standard_cost)
                    rail_costs[key]["first"] = min(rail_costs[key]["first"], first_cost)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # Create MetroStation nodes
        for station in metro_stations:
            session.run(
                """
                CREATE (:MetroStation {
                    station_id: $station_id,
                    name: $name,
                    lines: $lines,
                    is_interchange_metro: $is_interchange_metro,
                    is_interchange_national_rail: $is_interchange_national_rail
                })
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"],
                is_interchange_metro=station["is_interchange_metro"],
                is_interchange_national_rail=station["is_interchange_national_rail"]
            )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        # Create NationalRailStation nodes
        for station in rail_stations:
            session.run(
                """
                CREATE (:NationalRailStation {
                    station_id: $station_id,
                    name: $name,
                    lines: $lines,
                    is_interchange_national_rail: $is_interchange_national_rail,
                    is_interchange_metro: $is_interchange_metro
                })
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"],
                is_interchange_national_rail=station["is_interchange_national_rail"],
                is_interchange_metro=station["is_interchange_metro"]
            )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        # Create CONNECTS_TO_ON_LINE relationships for metro stations
        metro_links_count = 0
        for station in metro_stations:
            for adjacent in station["adjacent_stations"]:
                from_id = station["station_id"]
                to_id = adjacent["station_id"]
                cost_usd = metro_costs.get((from_id, to_id), 1.10)  # default fallback
                session.run(
                    """
                    MATCH (from:MetroStation {station_id: $from_id})
                    MATCH (to:MetroStation {station_id: $to_id})
                    CREATE (from)-[:CONNECTS_TO_ON_LINE {
                        line: $line,
                        travel_time_min: $travel_time_min,
                        cost_usd: $cost_usd
                    }]->(to)
                    """,
                    from_id=from_id,
                    to_id=to_id,
                    line=adjacent["line"],
                    travel_time_min=adjacent["travel_time_min"],
                    cost_usd=cost_usd
                )
                metro_links_count += 1
        print(f"  Created {metro_links_count} Metro CONNECTS_TO_ON_LINE relationships")

        # Create CONNECTS_TO_ON_LINE relationships for national rail stations
        rail_links_count = 0
        for station in rail_stations:
            for adjacent in station["adjacent_stations"]:
                from_id = station["station_id"]
                to_id = adjacent["station_id"]
                costs = rail_costs.get((from_id, to_id), {"standard": 4.00, "first": 6.50})  # default fallback
                session.run(
                    """
                    MATCH (from:NationalRailStation {station_id: $from_id})
                    MATCH (to:NationalRailStation {station_id: $to_id})
                    CREATE (from)-[:CONNECTS_TO_ON_LINE {
                        line: $line,
                        travel_time_min: $travel_time_min,
                        cost_standard_usd: $cost_standard,
                        cost_first_usd: $cost_first
                    }]->(to)
                    """,
                    from_id=from_id,
                    to_id=to_id,
                    line=adjacent["line"],
                    travel_time_min=adjacent["travel_time_min"],
                    cost_standard=costs["standard"],
                    cost_first=costs["first"]
                )
                rail_links_count += 1
        print(f"  Created {rail_links_count} National Rail CONNECTS_TO_ON_LINE relationships")

        # Create INTERCHANGE_WITH relationships between metro and rail stations
        interchange_count = 0
        for station in metro_stations:
            if station["is_interchange_national_rail"] and station["interchange_national_rail_station_id"]:
                session.run(
                    """
                    MATCH (metro:MetroStation {station_id: $metro_id})
                    MATCH (rail:NationalRailStation {station_id: $rail_id})
                    CREATE (metro)-[:INTERCHANGE_WITH]->(rail)
                    """,
                    metro_id=station["station_id"],
                    rail_id=station["interchange_national_rail_station_id"]
                )
                interchange_count += 1
        print(f"  Created {interchange_count} INTERCHANGE_WITH relationships")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()

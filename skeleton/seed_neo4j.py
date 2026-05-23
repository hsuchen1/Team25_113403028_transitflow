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

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # ─────────────────────────────────────────────────────────────────
        # 1. Create Metro Station nodes
        # ─────────────────────────────────────────────────────────────────
        print("  Creating metro station nodes...")
        for station in metro_stations:
            session.run(
                """
                MERGE (s:Station {station_id: $station_id})
                SET s.name = $name,
                    s.network = 'metro',
                    s.lines = $lines,
                    s.is_interchange_metro = true,
                    s.interchange_national_rail_id = $interchange_rail_id
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"],
                interchange_rail_id=station.get("interchange_national_rail_station_id")
            )
        print(f"    Created {len(metro_stations)} metro stations")

        # ─────────────────────────────────────────────────────────────────
        # 2. Create National Rail Station nodes
        # ─────────────────────────────────────────────────────────────────
        print("  Creating national rail station nodes...")
        for station in rail_stations:
            session.run(
                """
                MERGE (s:Station {station_id: $station_id})
                SET s.name = $name,
                    s.network = 'national_rail',
                    s.lines = $lines,
                    s.is_interchange_rail = true,
                    s.interchange_metro_id = $interchange_metro_id
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"],
                interchange_metro_id=station.get("interchange_metro_station_id")
            )
        print(f"    Created {len(rail_stations)} national rail stations")

        # ─────────────────────────────────────────────────────────────────
        # 3. Create Metro ROUTE relationships
        # ─────────────────────────────────────────────────────────────────
        print("  Creating metro route relationships...")
        metro_route_count = 0
        for station in metro_stations:
            from_id = station["station_id"]
            for adjacent in station.get("adjacent_stations", []):
                to_id = adjacent["station_id"]
                line = adjacent["line"]
                travel_time = adjacent["travel_time_min"]
                
                # 創建雙向關係（因為地鐵可以雙向行駛）
                session.run(
                    """
                    MATCH (from:Station {station_id: $from_id})
                    MATCH (to:Station {station_id: $to_id})
                    MERGE (from)-[r:ROUTE {line: $line, network: 'metro'}]->(to)
                    SET r.travel_time_min = $travel_time
                    """,
                    from_id=from_id,
                    to_id=to_id,
                    line=line,
                    travel_time=travel_time
                )
                metro_route_count += 1
        print(f"    Created {metro_route_count} metro routes")

        # ─────────────────────────────────────────────────────────────────
        # 4. Create National Rail ROUTE relationships
        # ─────────────────────────────────────────────────────────────────
        print("  Creating national rail route relationships...")
        rail_route_count = 0
        for station in rail_stations:
            from_id = station["station_id"]
            for adjacent in station.get("adjacent_stations", []):
                to_id = adjacent["station_id"]
                line = adjacent["line"]
                travel_time = adjacent["travel_time_min"]
                
                session.run(
                    """
                    MATCH (from:Station {station_id: $from_id})
                    MATCH (to:Station {station_id: $to_id})
                    MERGE (from)-[r:ROUTE {line: $line, network: 'national_rail'}]->(to)
                    SET r.travel_time_min = $travel_time
                    """,
                    from_id=from_id,
                    to_id=to_id,
                    line=line,
                    travel_time=travel_time
                )
                rail_route_count += 1
        print(f"    Created {rail_route_count} national rail routes")

        # ─────────────────────────────────────────────────────────────────
        # 5. Create INTERCHANGE relationships (metro ↔ national rail)
        # ─────────────────────────────────────────────────────────────────
        print("  Creating interchange relationships...")
        interchange_count = 0
        for metro_station in metro_stations:
            metro_id = metro_station["station_id"]
            rail_id = metro_station.get("interchange_national_rail_station_id")
            
            if rail_id:  # 如果此地鐵站有換乘到國鐵的選項
                session.run(
                    """
                    MATCH (metro:Station {station_id: $metro_id})
                    MATCH (rail:Station {station_id: $rail_id})
                    MERGE (metro)-[i:INTERCHANGE {type: 'metro_to_rail'}]-(rail)
                    """,
                    metro_id=metro_id,
                    rail_id=rail_id
                )
                interchange_count += 1
        print(f"    Created {interchange_count} interchange relationships")

        # ─────────────────────────────────────────────────────────────────
        # 6. Create indices for faster queries
        # ─────────────────────────────────────────────────────────────────
        print("  Creating graph indices...")
        session.run("CREATE INDEX station_id IF NOT EXISTS FOR (s:Station) ON (s.station_id)")
        session.run("CREATE INDEX station_network IF NOT EXISTS FOR (s:Station) ON (s.network)")
        print("    Index creation complete")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")



if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()

"""
Neo4j graph storage for lineage data.

Stores LineageGraph nodes and edges in Neo4j and provides
query methods for upstream/downstream traversal, impact analysis,
and full-graph retrieval.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from neo4j import GraphDatabase, Session

from src.config import settings
from src.models import LineageGraph

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Manages lineage data persistence in Neo4j."""

    def __init__(
        self,
        uri: str = settings.neo4j_uri,
        user: str = settings.neo4j_user,
        password: str = settings.neo4j_password,
    ):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    @contextmanager
    def _session(self):
        session: Session = self._driver.session()
        try:
            yield session
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------
    def setup_indexes(self) -> None:
        """Create indexes and constraints for lineage nodes."""
        queries = [
            "CREATE INDEX IF NOT EXISTS FOR (n:LineageNode) ON (n.id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:LineageNode) ON (n.node_type)",
            "CREATE INDEX IF NOT EXISTS FOR (n:LineageNode) ON (n.name)",
        ]
        with self._session() as session:
            for q in queries:
                session.run(q)
        logger.info("Neo4j indexes created")

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------
    def clear_all(self) -> None:
        """Delete all nodes and relationships (use with caution)."""
        with self._session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.info("Neo4j database cleared")

    def store_graph(self, graph: LineageGraph) -> dict:
        """
        Persist a LineageGraph into Neo4j using MERGE to avoid duplicates.
        Returns counts of nodes and edges written.
        """
        node_count = 0
        edge_count = 0

        with self._session() as session:
            # Upsert nodes
            for node in graph.nodes:
                session.run(
                    """
                    MERGE (n:LineageNode {id: $id})
                    SET n.name = $name,
                        n.node_type = $node_type,
                        n.metadata = $metadata
                    """,
                    id=node.id,
                    name=node.name,
                    node_type=node.node_type.value,
                    metadata=str(node.metadata),
                )
                # Also add a secondary label for the node type
                session.run(
                    f"MATCH (n:LineageNode {{id: $id}}) SET n:{node.node_type.value}",
                    id=node.id,
                )
                node_count += 1

            # Upsert edges
            for edge in graph.edges:
                session.run(
                    f"""
                    MATCH (a:LineageNode {{id: $source_id}})
                    MATCH (b:LineageNode {{id: $target_id}})
                    MERGE (a)-[r:{edge.edge_type.value}]->(b)
                    SET r.transformation = $transformation,
                        r.confidence = $confidence,
                        r.metadata = $metadata
                    """,
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    transformation=edge.transformation,
                    confidence=edge.confidence,
                    metadata=str(edge.metadata),
                )
                edge_count += 1

        logger.info("Stored %d nodes and %d edges in Neo4j", node_count, edge_count)
        return {"nodes_written": node_count, "edges_written": edge_count}

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------
    def get_full_graph(self) -> dict:
        """Return all nodes and edges as JSON-serializable dicts."""
        with self._session() as session:
            nodes_result = session.run(
                "MATCH (n:LineageNode) RETURN n.id AS id, n.name AS name, n.node_type AS node_type, n.metadata AS metadata"
            )
            nodes = [dict(record) for record in nodes_result]

            edges_result = session.run(
                """
                MATCH (a:LineageNode)-[r]->(b:LineageNode)
                RETURN a.id AS source_id, b.id AS target_id,
                       type(r) AS edge_type,
                       r.transformation AS transformation,
                       r.confidence AS confidence
                """
            )
            edges = [dict(record) for record in edges_result]

        return {"nodes": nodes, "edges": edges}

    def get_upstream(self, node_id: str, max_depth: int = 10) -> dict:
        """
        Trace upstream lineage: given a target column/table, find all sources.
        Follows DERIVES_FROM and READS_FROM edges backward.
        """
        with self._session() as session:
            result = session.run(
                """
                MATCH path = (start:LineageNode {id: $node_id})-[:DERIVES_FROM|READS_FROM*1..""" + str(max_depth) + """]->(upstream)
                UNWIND nodes(path) AS n
                UNWIND relationships(path) AS r
                WITH collect(DISTINCT {id: n.id, name: n.name, node_type: n.node_type}) AS nodes,
                     collect(DISTINCT {source: startNode(r).id, target: endNode(r).id, type: type(r), confidence: r.confidence}) AS edges
                RETURN nodes, edges
                """,
                node_id=node_id,
            )
            record = result.single()
            if record:
                return {"nodes": record["nodes"], "edges": record["edges"]}
            return {"nodes": [], "edges": []}

    def get_downstream(self, node_id: str, max_depth: int = 10) -> dict:
        """
        Trace downstream impact: given a source column/table, find all dependents.
        Follows DERIVES_FROM edges in reverse.
        """
        with self._session() as session:
            result = session.run(
                """
                MATCH path = (downstream)-[:DERIVES_FROM|READS_FROM*1..""" + str(max_depth) + """]->(start:LineageNode {id: $node_id})
                UNWIND nodes(path) AS n
                UNWIND relationships(path) AS r
                WITH collect(DISTINCT {id: n.id, name: n.name, node_type: n.node_type}) AS nodes,
                     collect(DISTINCT {source: startNode(r).id, target: endNode(r).id, type: type(r), confidence: r.confidence}) AS edges
                RETURN nodes, edges
                """,
                node_id=node_id,
            )
            record = result.single()
            if record:
                return {"nodes": record["nodes"], "edges": record["edges"]}
            return {"nodes": [], "edges": []}

    def search_nodes(self, query: str) -> list[dict]:
        """Full-text search on node names."""
        with self._session() as session:
            result = session.run(
                """
                MATCH (n:LineageNode)
                WHERE toLower(n.id) CONTAINS toLower($query)
                   OR toLower(n.name) CONTAINS toLower($query)
                RETURN n.id AS id, n.name AS name, n.node_type AS node_type
                ORDER BY n.id
                LIMIT 50
                """,
                query=query,
            )
            return [dict(record) for record in result]

    def get_impact_analysis(self, node_id: str) -> dict:
        """
        Impact analysis: if this node changes, what downstream assets are affected?
        Returns affected tables, views, columns, and reports.
        """
        downstream = self.get_downstream(node_id)
        affected_tables = set()
        affected_columns = set()

        for node in downstream.get("nodes", []):
            if node.get("node_type") in ("TABLE", "VIEW"):
                affected_tables.add(node["id"])
            elif node.get("node_type") == "COLUMN":
                affected_columns.add(node["id"])

        return {
            "source": node_id,
            "affected_tables": sorted(affected_tables),
            "affected_columns": sorted(affected_columns),
            "total_affected": len(affected_tables) + len(affected_columns),
            "full_graph": downstream,
        }

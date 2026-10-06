from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def verify_connectivity(self) -> bool:
        self._driver.verify_connectivity()
        return True

    def ensure_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE",
            "CREATE CONSTRAINT import_module_unique IF NOT EXISTS FOR (i:Import) REQUIRE i.module IS UNIQUE",
            "CREATE INDEX function_name_index IF NOT EXISTS FOR (f:Function) ON (f.name)",
            "CREATE INDEX class_name_index IF NOT EXISTS FOR (c:Class) ON (c.name)",
        ]
        with self._driver.session() as session:
            for statement in statements:
                session.run(statement).consume()

    def health(self) -> dict[str, object]:
        try:
            self.verify_connectivity()
            return {"connected": True, "uri": self.uri}
        except Exception as exc:
            return {"connected": False, "uri": self.uri, "error": str(exc)}

    def execute_query(self, cypher: str, **params: Any) -> Any:
        with self._driver.session() as session:
            return session.run(cypher, **params)

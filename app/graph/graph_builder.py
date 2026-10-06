from __future__ import annotations

from dataclasses import asdict
from typing import Any


class GraphBuilder:
    def __init__(self, client: object) -> None:
        self.client = client

    def upsert_parsed_files(self, parsed_files: list[dict[str, Any]]) -> dict[str, int]:
        existing_hashes = self._existing_hashes([record["path"] if isinstance(record, dict) else record.path for record in parsed_files])
        files_written = 0
        files_skipped = 0
        nodes_written = 0
        relationships_written = 0

        for parsed_file in parsed_files:
            record = asdict(parsed_file) if hasattr(parsed_file, "__dataclass_fields__") else parsed_file
            if existing_hashes.get(record["path"]) == record.get("hash"):
                files_skipped += 1
                continue
            self._run(
                """
                MERGE (f:File {path: $path})
                SET f.language = $language, f.hash = $hash
                """,
                path=record["path"],
                language=record.get("language", ""),
                hash=record.get("hash", ""),
            )
            files_written += 1

            for entity in record.get("entities", []):
                kind = entity.get("kind")
                if kind == "Function":
                    self._run(
                        """
                        MERGE (n:Function {file: $file, name: $name, start_line: $start_line, end_line: $end_line})
                        SET n.signature = $signature, n.docstring = $docstring, n.class_name = $class_name
                        WITH n
                        MATCH (f:File {path: $file})
                        MERGE (f)-[:DEFINES]->(n)
                        """,
                        file=record["path"],
                        name=entity.get("name", ""),
                        start_line=entity.get("start_line", 0),
                        end_line=entity.get("end_line", 0),
                        signature=entity.get("signature", ""),
                        docstring=entity.get("docstring", ""),
                        class_name=entity.get("class_name", ""),
                    )
                    nodes_written += 1
                elif kind == "Class":
                    self._run(
                        """
                        MERGE (n:Class {file: $file, name: $name})
                        SET n.bases = $bases, n.start_line = $start_line, n.end_line = $end_line
                        WITH n
                        MATCH (f:File {path: $file})
                        MERGE (f)-[:DEFINES]->(n)
                        """,
                        file=record["path"],
                        name=entity.get("name", ""),
                        bases=entity.get("bases", []),
                        start_line=entity.get("start_line", 0),
                        end_line=entity.get("end_line", 0),
                    )
                    nodes_written += 1
                elif kind == "Import":
                    self._run(
                        """
                        MERGE (n:Import {module: $module})
                        WITH n
                        MATCH (f:File {path: $file})
                        MERGE (f)-[:IMPORTS]->(n)
                        """,
                        file=record["path"],
                        module=entity.get("module", ""),
                    )
                    nodes_written += 1

            for relation in record.get("relations", []):
                kind = relation.get("kind")
                source = relation.get("source", "")
                target = relation.get("target", "")
                if not source or not target:
                    continue
                if kind == "CALLS":
                    self._run(
                        """
                        MATCH (source {name: $source})
                        MATCH (target {name: $target})
                        MERGE (source)-[:CALLS]->(target)
                        """,
                        source=source,
                        target=target,
                    )
                    relationships_written += 1
                elif kind == "EXTENDS":
                    self._run(
                        """
                        MATCH (source:Class {name: $source})
                        MATCH (target:Class {name: $target})
                        MERGE (source)-[:EXTENDS]->(target)
                        """,
                        source=source,
                        target=target,
                    )
                    relationships_written += 1
                elif kind == "HAS_METHOD":
                    self._run(
                        """
                        MATCH (source:Class {name: $source})
                        MATCH (target:Function {name: $target})
                        MERGE (source)-[:HAS_METHOD]->(target)
                        """,
                        source=source,
                        target=target,
                    )
                    relationships_written += 1

        return {
            "files_processed": files_written,
            "files_skipped": files_skipped,
            "nodes_written": nodes_written,
            "relationships_written": relationships_written,
        }

    def _existing_hashes(self, paths: list[str]) -> dict[str, str]:
        if not paths or self.client is None:
            return {}
        cypher = """
        MATCH (f:File)
        WHERE f.path IN $paths
        RETURN f.path AS path, f.hash AS hash
        """
        rows = self._run(cypher, paths=paths)
        if rows is None:
            return {}
        if hasattr(rows, "data"):
            rows = rows.data()
        if isinstance(rows, list):
            result: dict[str, str] = {}
            for row in rows:
                if isinstance(row, dict) and row.get("path"):
                    result[str(row["path"])] = str(row.get("hash", ""))
            return result
        return {}

    def _run(self, cypher: str, **params: Any) -> Any:
        if self.client is None:
            return None
        if hasattr(self.client, "execute_query"):
            return self.client.execute_query(cypher, **params)
        if hasattr(self.client, "session"):
            with self.client.session() as session:
                return session.run(cypher, **params)
        if callable(self.client):
            return self.client(cypher, **params)
        return None

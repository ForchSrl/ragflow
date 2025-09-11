import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pyodbc


@dataclass
class Profile:
    host: str
    user: str
    password: str
    port: Optional[int] = None
    driver: str = "ODBC Driver 17 for SQL Server"
    default_db: Optional[str] = None


def resolve_profile(profile_name: str) -> Profile:
    """Resolve env-based connection profile.

    Example: profile "mssql_rag" -> env MSSQL_RAG_HOST/USER/PASSWORD/PORT/DRIVER/DEFAULT_DB
    """
    prefix = re.sub(r"[^A-Za-z0-9]", "_", profile_name or "").upper()
    host = os.environ.get(f"{prefix}_HOST", "")
    user = os.environ.get(f"{prefix}_USER", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    port = os.environ.get(f"{prefix}_PORT", "")
    driver = os.environ.get(f"{prefix}_DRIVER", "ODBC Driver 17 for SQL Server")
    default_db = os.environ.get(f"{prefix}_DEFAULT_DB", "") or None
    return Profile(host=host, user=user, password=password, port=int(port) if str(port).isdigit() else None, driver=driver, default_db=default_db)


def connect(profile: Profile, database: Optional[str] = None, timeout: int = 10) -> pyodbc.Connection:
    """Create a pyodbc connection for SQL Server using profile and optional database override."""
    target_db = database or profile.default_db or "master"
    server = profile.host if "\\" in profile.host and not profile.port else (f"{profile.host},{profile.port}" if profile.port else profile.host)
    cs = (
        f"DRIVER={{{profile.driver}}};SERVER={server};DATABASE={target_db};UID={profile.user};PWD={profile.password};TrustServerCertificate=yes;"
    )
    return pyodbc.connect(cs, timeout=timeout)


def fetchall_dict(cursor) -> List[Dict[str, Any]]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def introspect_schema(conn: pyodbc.Connection) -> Dict[str, Any]:
    """Return schema info: tables, columns, primary keys, foreign keys."""
    cur = conn.cursor()

    # Tables
    cur.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE IN ('BASE TABLE','VIEW')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
    )
    tables = fetchall_dict(cur)

    # Columns
    cur.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """
    )
    columns = fetchall_dict(cur)

    # Primary keys
    cur.execute(
        """
        SELECT KU.TABLE_SCHEMA, KU.TABLE_NAME, KU.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS TC
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS KU
             ON TC.CONSTRAINT_NAME = KU.CONSTRAINT_NAME
        WHERE TC.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ORDER BY KU.TABLE_SCHEMA, KU.TABLE_NAME, KU.ORDINAL_POSITION
        """
    )
    pk = fetchall_dict(cur)

    # Foreign keys (simplified)
    cur.execute(
        """
        SELECT 
            FK.CONSTRAINT_NAME,
            FK.TABLE_SCHEMA AS FK_SCHEMA,
            FK.TABLE_NAME   AS FK_TABLE,
            CU.COLUMN_NAME  AS FK_COLUMN,
            PK.TABLE_SCHEMA AS PK_SCHEMA,
            PK.TABLE_NAME   AS PK_TABLE,
            PT.COLUMN_NAME  AS PK_COLUMN
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS C
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS FK ON C.CONSTRAINT_NAME = FK.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS PK ON C.UNIQUE_CONSTRAINT_NAME = PK.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE CU ON C.CONSTRAINT_NAME = CU.CONSTRAINT_NAME
        JOIN (
            SELECT i1.TABLE_NAME, i1.CONSTRAINT_NAME, i2.COLUMN_NAME, i1.TABLE_SCHEMA
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS i1
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE i2 ON i1.CONSTRAINT_NAME = i2.CONSTRAINT_NAME
            WHERE i1.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ) PT ON PT.CONSTRAINT_NAME = PK.CONSTRAINT_NAME
        ORDER BY FK.TABLE_SCHEMA, FK.TABLE_NAME
        """
    )
    fks = fetchall_dict(cur)

    # Build index for quick lookups
    col_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for c in columns:
        col_map.setdefault((c["TABLE_SCHEMA"], c["TABLE_NAME"]), []).append(c)

    pk_map: Dict[Tuple[str, str], List[str]] = {}
    for r in pk:
        pk_map.setdefault((r["TABLE_SCHEMA"], r["TABLE_NAME"]), []).append(r["COLUMN_NAME"])

    # Assemble tables with columns and keys
    table_docs: List[Dict[str, Any]] = []
    for t in tables:
        key = (t["TABLE_SCHEMA"], t["TABLE_NAME"])
        table_docs.append(
            {
                "schema": t["TABLE_SCHEMA"],
                "name": t["TABLE_NAME"],
                "type": t["TABLE_TYPE"],
                "primary_key": pk_map.get(key, []),
                "columns": col_map.get(key, []),
            }
        )

    return {"tables": table_docs, "foreign_keys": fks}


def to_markdown(schema: Dict[str, Any], db_name: str) -> str:
    """Produce a concise Markdown document suitable for KB ingestion."""
    lines: List[str] = []
    lines.append(f"# Database: {db_name}")
    for t in schema.get("tables", []):
        lines.append("")
        lines.append(f"## {t['schema']}.{t['name']} ({t['type']})")
        if t.get("primary_key"):
            lines.append(f"Primary Key: {', '.join(t['primary_key'])}")
        lines.append("")
        lines.append("| Column | Type | Nullable | Default |")
        lines.append("|--|--|--|--|")
        for c in t.get("columns", []):
            lines.append(
                f"| {c['COLUMN_NAME']} | {c['DATA_TYPE']} | {c['IS_NULLABLE']} | {c.get('COLUMN_DEFAULT') or ''} |"
            )
    if schema.get("foreign_keys"):
        lines.append("")
        lines.append("## Foreign Keys")
        lines.append("| FK Table | FK Column | PK Table | PK Column |")
        lines.append("|--|--|--|--|")
        for fk in schema["foreign_keys"]:
            lines.append(
                f"| {fk['FK_SCHEMA']}.{fk['FK_TABLE']} | {fk['FK_COLUMN']} | {fk['PK_SCHEMA']}.{fk['PK_TABLE']} | {fk['PK_COLUMN']} |"
            )
    return "\n".join(lines)


def build_join_graph(schema: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """Return adjacency list of joinable tables based on FKs."""
    graph: Dict[str, List[Dict[str, str]]] = {}
    for fk in schema.get("foreign_keys", []):
        a = f"{fk['FK_SCHEMA']}.{fk['FK_TABLE']}"
        b = f"{fk['PK_SCHEMA']}.{fk['PK_TABLE']}"
        edge = {
            "from": a,
            "to": b,
            "on": f"{fk['FK_SCHEMA']}.{fk['FK_TABLE']}.{fk['FK_COLUMN']} = {fk['PK_SCHEMA']}.{fk['PK_TABLE']}.{fk['PK_COLUMN']}",
        }
        graph.setdefault(a, []).append(edge)
        graph.setdefault(b, []).append({"from": b, "to": a, "on": edge["on"]})
    return graph


def export_schema_markdown(profile_name: str, database: str, out_path: str) -> str:
    """Introspect `database` using `profile_name` and write a markdown file to `out_path`.
    Returns the file path.
    """
    prof = resolve_profile(profile_name)
    with connect(prof, database) as conn:
        sch = introspect_schema(conn)
    md = to_markdown(sch, database)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    return out_path


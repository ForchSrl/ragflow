#
#  Copyright 2025 The InfiniFlow Authors.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
import os
import re
from abc import ABC
from typing import List, Dict

import pyodbc

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.utils.api_utils import timeout


class SchemaProbeParam(ToolParamBase):
    """
    Tool to probe SQL Server schema: columns, foreign keys, sample values.
    """

    def __init__(self):
        # LLM-callable parameters
        self.meta: ToolMeta = {
            "name": "schema_probe",
            "description": "Inspect table schema in SQL Server (columns, FKs, samples).",
            "parameters": {
                "table": {
                    "type": "string",
                    "description": "Target table name. Accepts 'schema.table' or just table.",
                    "required": True,
                },
                "op": {
                    "type": "string",
                    "description": "Operation: columns | fks | sample",
                    "required": True,
                },
                "limit": {
                    "type": "number",
                    "description": "Row limit for sample (default 3)",
                    "required": False,
                },
            },
        }
        super().__init__()
        # Connection defaults (env-based profile preferred)
        self.db_type = "mssql"
        self.connection_profile = "mssql_rag"
        self.database = ""  # fallback to profile DEFAULT_DB
        self.host = ""
        self.port = 0
        self.username = ""
        self.password = ""
        self.outputs = {  # standard outputs
            "formalized_content": {"type": "string", "value": ""},
            "json": {"type": "Array<Object>", "value": []},
        }

    def check(self):
        pass


class SchemaProbe(ToolBase, ABC):
    component_name = "SchemaProbe"

    @timeout(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60))
    def _invoke(self, **kwargs):
        table = (kwargs.get("table") or "").strip()
        op = (kwargs.get("op") or "").strip().lower()
        limit = int(kwargs.get("limit") or 3)
        if not table or op not in {"columns", "fks", "sample"}:
            raise ValueError("schema_probe requires: table, op in {columns|fks|sample}")

        prof = self._resolve_profile(getattr(self._param, 'connection_profile', '') or '')
        database = self._param.database or prof.get('database') or "master"
        conn = self._connect(
            host=prof.get('host') or self._param.host,
            port=prof.get('port') or self._param.port,
            user=prof.get('user') or self._param.username,
            password=prof.get('password') or self._param.password,
            database=database,
        )

        try:
            schema_name, table_name = self._split_table(table)
            if op == "columns":
                rows = self._columns(conn, schema_name, table_name)
                self._emit(rows, self._fmt_columns(schema_name, table_name, rows))
            elif op == "fks":
                rows = self._fks(conn, schema_name, table_name)
                self._emit(rows, self._fmt_fks(schema_name, table_name, rows))
            else:  # sample
                rows = self._sample(conn, schema_name, table_name, limit)
                self._emit(rows, self._fmt_sample(schema_name, table_name, rows))
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return self.output("formalized_content")

    def _emit(self, rows: List[Dict], md: str):
        self.set_output("json", rows)
        self.set_output("formalized_content", md)

    def _resolve_profile(self, profile: str) -> dict:
        if not profile:
            return {}
        prefix = re.sub(r"[^A-Za-z0-9]", "_", profile).upper()
        return {
            'host': os.environ.get(f"{prefix}_HOST", ""),
            'user': os.environ.get(f"{prefix}_USER", ""),
            'password': os.environ.get(f"{prefix}_PASSWORD", ""),
            'port': int(os.environ.get(f"{prefix}_PORT", "0") or 0),
            'database': os.environ.get(f"{prefix}_DEFAULT_DB", ""),
        }

    def _connect(self, host: str, port: int, user: str, password: str, database: str):
        server = host if '\\' in host and not port else (f"{host},{port}" if port else host)
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={database};UID={user};PWD={password};TrustServerCertificate=yes;"
        )
        return pyodbc.connect(conn_str, timeout=10)

    def _split_table(self, tb: str):
        parts = tb.split('.')
        if len(parts) == 1:
            return 'dbo', parts[0]
        return parts[0], parts[1]

    def _columns(self, conn, schema, table):
        c = conn.cursor()
        c.execute(
            """
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH, COLUMN_DEFAULT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA=? AND TABLE_NAME=?
            ORDER BY ORDINAL_POSITION
            """,
            (schema, table),
        )
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]

    def _fks(self, conn, schema, table):
        c = conn.cursor()
        c.execute(
            """
            SELECT CU.COLUMN_NAME AS FK_COLUMN,
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
            WHERE FK.TABLE_SCHEMA=? AND FK.TABLE_NAME=?
            ORDER BY PK.TABLE_SCHEMA, PK.TABLE_NAME
            """,
            (schema, table),
        )
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]

    def _sample(self, conn, schema, table, limit):
        c = conn.cursor()
        c.execute(f"SELECT TOP {int(limit)} * FROM [{schema}].[{table}]")
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]

    def _fmt_columns(self, schema, table, rows):
        md = [f"# Columns for {schema}.{table}", "", "| Column | Type | Nullable | Default |", "|--|--|--|--|"]
        for r in rows:
            md.append(f"| {r['COLUMN_NAME']} | {r['DATA_TYPE']} | {r['IS_NULLABLE']} | {r.get('COLUMN_DEFAULT') or ''} |")
        return "\n".join(md)

    def _fmt_fks(self, schema, table, rows):
        md = [f"# Foreign Keys for {schema}.{table}", "", "| FK Column | Ref Table | Ref Column |", "|--|--|--|"]
        for r in rows:
            md.append(f"| {r['FK_COLUMN']} | {r['PK_SCHEMA']}.{r['PK_TABLE']} | {r['PK_COLUMN']} |")
        return "\n".join(md)

    def _fmt_sample(self, schema, table, rows):
        md = [f"# Sample Rows from {schema}.{table}"]
        for r in rows:
            line = ", ".join([f"{k}={str(v)[:60]}" for k, v in r.items()])
            md.append(f"- {line}")
        return "\n".join(md)

    def thoughts(self) -> str:
        return "Probing database schema..."


#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import os
import re
from abc import ABC
import pandas as pd
import pymysql
import psycopg2
import pyodbc
from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.utils.api_utils import timeout


class ExeSQLParam(ToolParamBase):
    """
    Define the ExeSQL component parameters.
    """

    def __init__(self):
        self.meta:ToolMeta = {
            "name": "execute_sql",
            "description": "Executes a single read-only SQL query against the configured database/profile. For MSSQL you can override the database per-query by adding a leading directive, e.g. `-- DB: MyDatabase` or `/* DB: MyDatabase */`.",
            "parameters": {
                "sql": {
                    "type": "string",
                    "description": "The SQL needs to be executed.",
                    "default": "{sys.query}",
                    "required": True
                }
            }
        }
        super().__init__()
        self.db_type = "mysql"
        self.database = ""
        self.username = ""
        self.host = ""
        self.port = 3306
        self.password = ""
        self.max_records = 1024
        # Optional profile name to resolve credentials from env/secrets
        # Example: "mssql_rag" -> MSSQL_RAG_HOST/USER/PASSWORD/PORT[/DRIVER]
        self.connection_profile = ""

    def check(self):
        self.check_valid_value(self.db_type, "Choose DB type", ['mysql', 'postgresql', 'mariadb', 'mssql'])
        # Database requirement rules:
        # - For MSSQL, allow empty database to enable 3-part names or runtime DB override
        # - For MySQL/MariaDB/PostgreSQL, require a database unless the connection_profile
        #   provides a DEFAULT_DB via env
        if not self.database and self.db_type not in ("mssql",):
            prof = getattr(self, 'connection_profile', "")
            if not prof or not os.environ.get(re.sub(r"[^A-Za-z0-9]", "_", prof).upper() + "_DEFAULT_DB", ""):
                self.check_empty(self.database, "Database name")
        # Require inline creds only when no profile is configured
        if not getattr(self, 'connection_profile', ""):
            self.check_empty(self.username, "database username")
            self.check_empty(self.host, "IP Address")
            self.check_positive_integer(self.port, "IP Port")
            self.check_empty(self.password, "Database password")
        self.check_positive_integer(self.max_records, "Maximum number of records")
        if self.database == "rag_flow":
            if self.host == "ragflow-mysql":
                raise ValueError("For the security reason, it dose not support database named rag_flow.")
            if self.password == "infini_rag_flow":
                raise ValueError("For the security reason, it dose not support database named rag_flow.")

    def get_input_form(self) -> dict[str, dict]:
        return {
            "sql": {
                "name": "SQL",
                "type": "line"
            }
        }


class ExeSQL(ToolBase, ABC):
    component_name = "ExeSQL"

    @timeout(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60))
    def _invoke(self, **kwargs):
        def resolve_profile(profile: str) -> dict:
            if not profile:
                return {}
            prefix = re.sub(r"[^A-Za-z0-9]", "_", profile).upper()
            return {
                'host': os.environ.get(f"{prefix}_HOST", ""),
                'user': os.environ.get(f"{prefix}_USER", ""),
                'password': os.environ.get(f"{prefix}_PASSWORD", ""),
                'port': int(os.environ.get(f"{prefix}_PORT", "0") or 0),
                'driver': os.environ.get(f"{prefix}_DRIVER", "ODBC Driver 17 for SQL Server"),
                'database': os.environ.get(f"{prefix}_DEFAULT_DB", ""),
            }

        def extract_db_override(sql_text: str) -> tuple[str, str]:
            """Extracts a leading DB override directive from sql and returns (db_name, stripped_sql).
            Supported forms at the very beginning (ignoring leading whitespace):
              -- DB: MyDatabase
              /* DB: MyDatabase */
            """
            text = sql_text or ""
            m = re.match(r"^\s*--\s*DB\s*:\s*([A-Za-z0-9_.$-]+)\s*(?:\r?\n|$)", text, re.IGNORECASE)
            if m:
                db = m.group(1)
                stripped = text[m.end():]
                return db, stripped
            m = re.match(r"^\s*/\*\s*DB\s*:\s*([A-Za-z0-9_.$-]+)\s*\*/\s*", text, re.IGNORECASE)
            if m:
                db = m.group(1)
                stripped = text[m.end():]
                return db, stripped
            return "", text
        def sanitize_sql(sql: str, db_type: str, max_records: int) -> str:
            s = (sql or "").replace('```', '').strip()
            # Strip a trailing semicolon
            if s.endswith(';'):
                s = s[:-1]
            # Only one statement allowed
            if ';' in s:
                raise Exception("Only a single SQL statement is allowed.")
            # Remove injected IDs like [ID:123]
            s = re.sub(r"\[ID:[0-9]+\]", "", s)
            # Must be read-only SELECT (optionally WITH CTE)
            if re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE|GRANT|EXEC|CALL|BULK INSERT|OPENROWSET|WRITETEXT|UPDATETEXT|DBCC|SHUTDOWN|BACKUP|RESTORE|RECONFIGURE|KILL|DENY|REVOKE|SET)\b", s, re.IGNORECASE):
                raise Exception("Only read-only SELECT queries are allowed.")
            if not re.match(r"^(\s*WITH\b|\s*SELECT\b)", s, re.IGNORECASE):
                raise Exception("Query must be a SELECT (optionally with CTE).")

            dbt = (db_type or '').lower()
            if dbt in ("mysql", "mariadb", "postgresql"):
                # Inject LIMIT if absent
                if not re.search(r"\bLIMIT\b", s, re.IGNORECASE):
                    s = f"{s} LIMIT {int(max_records or 1000)}"
            elif dbt == "mssql":
                # Normalize backticks/double quotes to [] and add TOP if not present
                def _bt_repl(m):
                    ident = m.group(1)
                    if "." in ident:
                        left, right = ident.split(".", 1)
                        return f"[{left}].[{right}]"
                    return f"[{ident}]"
                s = re.sub(r"`([^`]+)`", _bt_repl, s)
                def _dq_repl(m):
                    ident = m.group(1)
                    if "." in ident:
                        left, right = ident.split(".", 1)
                        return f"[{left}].[{right}]"
                    return f"[{ident}]"
                s = re.sub(r'"([^"]+)"', _dq_repl, s)
                # Remove MySQL LIMIT if mistakenly present and use TOP
                lim_match = re.search(r"\bLIMIT\s+(\d+)\b", s, re.IGNORECASE)
                cap = int(max_records or 1000)
                if lim_match:
                    try:
                        cap = min(cap, int(lim_match.group(1)))
                    except Exception:
                        pass
                    s = re.sub(r"\s+LIMIT\s+\d+\b\s*;?\s*$", "", s, flags=re.IGNORECASE)
                # Inject TOP if neither TOP nor OFFSET/FETCH is present
                if not re.search(r"\bTOP\b|\bFETCH\s+NEXT\b", s, re.IGNORECASE):
                    if re.match(r"\s*SELECT\s+DISTINCT\b", s, re.IGNORECASE):
                        s = re.sub(r"^\s*SELECT\s+DISTINCT\s+", f"SELECT DISTINCT TOP {cap} ", s, flags=re.IGNORECASE)
                    else:
                        s = re.sub(r"^\s*SELECT\s+", f"SELECT TOP {cap} ", s, flags=re.IGNORECASE)
            else:
                # Default: return as-is for unknown types
                pass
            return s

        def convert_decimals(obj):
            from decimal import Decimal
            if isinstance(obj, Decimal):
                return float(obj)  # 或 str(obj)
            elif isinstance(obj, dict):
                return {k: convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_decimals(item) for item in obj]
            return obj

        sql = kwargs.get("sql")
        if not sql:
            raise Exception("SQL for `ExeSQL` MUST not be empty.")
        # Resolve DB override directive (if present) and strip it from SQL
        db_override, sql = extract_db_override(sql)

        # Resolve profile first
        profile_conf = resolve_profile(getattr(self._param, 'connection_profile', '') or '')

        # Try to sanitize; if it fails, attempt to salvage a runnable SELECT
        def _salvage_sql(orig: str) -> tuple[str, bool, str]:
            reason = ""
            try:
                return sanitize_sql(orig, self._param.db_type, int(self._param.max_records or 1000)), False, reason
            except Exception as e:
                reason = f"sanitize failed: {e}"
                # Try to extract the first SELECT (or WITH ... SELECT) from the text
                m = re.search(r"(?is)\bwith\b\s+.*?\bselect\b.*", orig or "")
                cand = m.group(0) if m else None
                if not cand:
                    m = re.search(r"(?is)\bselect\b.*", orig or "")
                    cand = m.group(0) if m else None
                if cand:
                    try:
                        cand2 = sanitize_sql(cand, self._param.db_type, int(self._param.max_records or 1000))
                        return cand2, True, reason
                    except Exception:
                        pass
                # Final fallback to keep flow alive
                return "SELECT 1", True, reason

        sql, used_fallback, fb_reason = _salvage_sql(sql)
        sqls = [sql]
        # Merge profile into connection parameters
        host = profile_conf.get('host') or self._param.host
        user = profile_conf.get('user') or self._param.username
        password = profile_conf.get('password') or self._param.password
        port = int(profile_conf.get('port') or self._param.port)
        database = db_override or self._param.database or profile_conf.get('database')

        if self._param.db_type in ["mysql", "mariadb"]:
            db = pymysql.connect(db=database, user=user, host=host, port=port, password=password)
        elif self._param.db_type == 'postgresql':
            db = psycopg2.connect(dbname=database, user=user, host=host, port=port, password=password)
        elif self._param.db_type == 'mssql':
            driver = profile_conf.get('driver') or 'ODBC Driver 17 for SQL Server'
            server = host if '\\' in host else f"{host},{port}" if port else host
            parts = [
                f"DRIVER={{{driver}}}",
                f"SERVER={server}",
            ]
            if database:
                parts.append(f"DATABASE={database}")
            parts.extend([
                f"UID={user}",
                f"PWD={password}",
                "TrustServerCertificate=yes",
            ])
            conn_str = ";".join(parts) + ";"
            db = pyodbc.connect(conn_str, timeout=10)
        try:
            cursor = db.cursor()
            try:
                cursor.timeout = int(os.environ.get('SQL_EXEC_TIMEOUT', '30'))
            except Exception:
                pass
        except Exception as e:
            raise Exception("Database Connection Failed! \n" + str(e))

        sql_res = []
        formalized_content = []
        for single_sql in sqls:
            single_sql = single_sql.replace('```','')
            if not single_sql:
                continue
            single_sql = re.sub(r"\[ID:[0-9]+\]", "", single_sql)
            try:
                cursor.execute(single_sql)
            except Exception as exec_err:
                # If execution fails, try one last minimal fallback
                if single_sql.strip().lower() != "select 1":
                    try:
                        cursor.execute("SELECT 1")
                        single_sql = "SELECT 1"
                        used_fallback = True
                        fb_reason = fb_reason or f"execution failed: {exec_err}"
                    except Exception:
                        raise
            if cursor.rowcount == 0:
                sql_res.append({"content": "No record in the database!"})
                break
            if self._param.db_type == 'mssql':
                single_res = pd.DataFrame.from_records(cursor.fetchmany(self._param.max_records),
                                                       columns=[desc[0] for desc in cursor.description])
            else:
                single_res = pd.DataFrame([i for i in cursor.fetchmany(self._param.max_records)])
                single_res.columns = [i[0] for i in cursor.description]

            for col in single_res.columns:
                if pd.api.types.is_datetime64_any_dtype(single_res[col]):
                    single_res[col] = single_res[col].dt.strftime('%Y-%m-%d')

            sql_res.append(convert_decimals(single_res.to_dict(orient='records')))
            formalized_content.append(single_res.to_markdown(index=False, floatfmt=".6f"))

        # expose effective SQL and whether fallback path was used
        try:
            self.set_output("sql", sql)
            self.set_output("used_fallback", used_fallback)
            if used_fallback and fb_reason:
                self.set_output("fallback_reason", fb_reason)
        except Exception:
            pass

        self.set_output("json", sql_res)
        self.set_output("formalized_content", "\n\n".join(formalized_content))
        return self.output("formalized_content")

    def thoughts(self) -> str:
        return "Query sent—waiting for the data."

import os
import json
import time
import logging
from abc import ABC
import requests

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.utils.api_utils import timeout

logger = logging.getLogger(__name__)


class LogSQLLearningParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "log_sql_learning",
            "description": "Append interaction (question, SQL, result) to local log and optionally a Ragflow dataset.",
            "parameters": {
                "question": {"type": "string", "description": "User NL question.", "default": "{sys.query}", "required": True},
                "sql": {"type": "string", "description": "Validated SQL.", "default": "", "required": True},
                "db": {"type": "string", "description": "Database name.", "default": "{sys.db}", "required": False},
                "result_markdown": {"type": "string", "description": "Result preview (markdown).", "default": "", "required": False},
                "agent_id": {"type": "string", "description": "Agent ID for attribution.", "default": "", "required": False},
                "learning_dataset_id": {"type": "string", "description": "Target dataset id to store interactions.", "default": "", "required": False},
                "learning_document_id": {"type": "string", "description": "Target document id to append chunks.", "default": "", "required": False},
            },
        }
        super().__init__()
        self.question = ""
        self.sql = ""
        self.db = ""
        self.result_markdown = ""
        self.agent_id = ""
        self.learning_dataset_id = ""
        self.learning_document_id = ""

    def check(self):
        self.check_empty(self.question, "question")
        self.check_empty(self.sql, "sql")


class LogSQLLearning(ToolBase, ABC):
    component_name = "LogSQLLearning"

    @timeout(os.environ.get("COMPONENT_EXEC_TIMEOUT", 30))
    def _invoke(self, **kwargs):
        q = (kwargs.get("question") or "").strip()
        sql = (kwargs.get("sql") or "").strip()
        db = (kwargs.get("db") or "").strip()
        preview = (kwargs.get("result_markdown") or "").strip()
        agent_id = (kwargs.get("agent_id") or os.environ.get("RAGFLOW_AGENT_ID", "")).strip()
        ds_id = (kwargs.get("learning_dataset_id") or os.environ.get("SQL_LEARNING_DATASET_ID", "")).strip()
        doc_id = (kwargs.get("learning_document_id") or os.environ.get("SQL_LEARNING_DOCUMENT_ID", "")).strip()

        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        block = [
            f"## {ts}",
            f"- Agent: {agent_id or 'n/a'}",
            f"- DB: {db or 'n/a'}",
            f"- Question: {q}",
            f"- SQL:\n\n{sql}\n",
        ]
        if preview:
            block.append(f"- Result (preview):\n\n{preview}\n")
        content = "\n".join(block) + "\n\n---\n\n"

        # Append locally
        try:
            os.makedirs("history_data_agent", exist_ok=True)
            with open("history_data_agent/sql_learning.md", "a", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.warning(f"Failed to append local learning log: {e}")

        # Optionally push to Ragflow dataset as a chunk
        base = os.environ.get("RAGFLOW_BASE_URL", "http://127.0.0.1:9380")
        api_key = os.environ.get("RAGFLOW_API_KEY", "")
        if not api_key:
            self.set_output("status", "logged_local_only")
            return "logged_local_only"

        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            session = requests.Session()
            # Create dataset if absent
            if not ds_id:
                r = session.post(f"{base}/api/v1/datasets", headers={**headers, "Content-Type": "application/json"}, json={"name": "SQL Learning"}, timeout=10)
                r.raise_for_status()
                ds_id = r.json()["data"]["id"]
            # Create a document if absent
            if not doc_id:
                files = {"file": ("interactions.md", content.encode("utf-8"), "text/markdown")}
                r = session.post(f"{base}/api/v1/datasets/{ds_id}/documents", headers=headers, files=files, timeout=20)
                r.raise_for_status()
                doc_id = r.json()["data"][0]["id"]
            else:
                # Append as a chunk
                r = session.post(
                    f"{base}/api/v1/datasets/{ds_id}/documents/{doc_id}/chunks",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"content": content},
                    timeout=20,
                )
                r.raise_for_status()
            # Persist ids for reuse
            try:
                with open("history_data_agent/sql_learning.dataset.json", "w", encoding="utf-8") as f:
                    json.dump({"dataset_id": ds_id, "document_id": doc_id}, f)
            except Exception:
                pass
            self.set_output("status", "logged_and_synced")
            return "logged_and_synced"
        except Exception as e:
            logger.warning(f"Failed to push learning chunk: {e}")
            self.set_output("status", "logged_local_only")
            return "logged_local_only"

    def thoughts(self) -> str:
        return "Recording interaction for continual learning."


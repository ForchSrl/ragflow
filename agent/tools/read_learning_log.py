import os
import re
import logging
from abc import ABC
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.utils.api_utils import timeout


logger = logging.getLogger(__name__)


class ReadLearningLogParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "read_learning_log",
            "description": "Read and parse the local SQL learning log; returns latest entries as JSON.",
            "parameters": {
                "limit": {"type": "integer", "description": "Max entries to return", "default": 5, "required": False},
                "contains": {"type": "string", "description": "Optional substring filter on question or SQL", "default": "", "required": False},
                "path": {"type": "string", "description": "Override log file path", "default": "history_data_agent/sql_learning.md", "required": False},
                "from_date": {"type": "string", "description": "Optional start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)", "default": "", "required": False},
                "to_date": {"type": "string", "description": "Optional end date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)", "default": "", "required": False},
            }
        }
        super().__init__()
        self.limit = 5
        self.contains = ""
        self.path = "history_data_agent/sql_learning.md"
        self.from_date = ""
        self.to_date = ""

    def check(self):
        if int(self.limit) <= 0:
            raise ValueError("limit must be positive")


class ReadLearningLog(ToolBase, ABC):
    component_name = "ReadLearningLog"

    @timeout(os.environ.get("COMPONENT_EXEC_TIMEOUT", 15))
    def _invoke(self, **kwargs):
        path = (kwargs.get("path") or self._param.path).strip()
        limit = int(kwargs.get("limit") or self._param.limit)
        contains = (kwargs.get("contains") or self._param.contains).strip().lower()
        from_date_str = (kwargs.get("from_date") or self._param.from_date).strip()
        to_date_str = (kwargs.get("to_date") or self._param.to_date).strip()

        if not os.path.exists(path):
            self.set_output("entries", [])
            return []

        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()

        # Parse optional date range
        def parse_dt(s: str) -> Optional[datetime]:
            if not s:
                return None
            fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
            for fmt in fmts:
                try:
                    return datetime.strptime(s, fmt)
                except Exception:
                    pass
            return None

        from_dt = parse_dt(from_date_str)
        to_dt = parse_dt(to_date_str)
        if to_dt and to_date_str and len(to_date_str) == 10:
            # If only date provided, treat as end of the day inclusive
            to_dt = to_dt.replace(hour=23, minute=59, second=59)

        # Iterate sections by explicit headers, capturing timestamp + block
        header_re = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$", re.MULTILINE)
        matches = list(header_re.finditer(txt))
        entries: List[Dict] = []
        for i in range(len(matches)):
            m = matches[i]
            ts = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(txt)
            block = txt[start:end].strip()

            q = self._extract_field(block, r"- Question:\s*(.*)")
            sql = self._extract_sql(block)
            res = self._extract_result(block)
            if not (q or sql or res):
                continue

            # Keyword filter (optional)
            if contains and (contains not in (q or '').lower()) and (contains not in (sql or '').lower()):
                continue

            # Date filter (optional)
            try:
                ts_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_dt = None
            if from_dt and ts_dt and ts_dt < from_dt:
                continue
            if to_dt and ts_dt and ts_dt > to_dt:
                continue

            entries.append({
                "timestamp": ts,
                "question": q,
                "sql": sql,
                "result_markdown": res,
            })

        # Sort by timestamp descending (most recent first), then apply limit
        def key_dt(e: Dict) -> Tuple[int, str]:
            try:
                return (int(datetime.strptime(e.get("timestamp", ""), "%Y-%m-%d %H:%M:%S").timestamp()), e.get("timestamp", ""))
            except Exception:
                return (0, e.get("timestamp", ""))

        entries = sorted(entries, key=key_dt, reverse=True)[:max(0, limit)]

        self.set_output("entries", entries)
        return entries

    @staticmethod
    def _extract_field(text: str, pattern: str) -> str:
        m = re.search(pattern, text)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_sql(text: str) -> str:
        m = re.search(r"- SQL:\n\n([\s\S]*?)\n- Result", text)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_result(text: str) -> str:
        m = re.search(r"- Result \(preview\):\n\n([\s\S]*?)$", text)
        return m.group(1).strip() if m else ""

    def thoughts(self) -> str:
        return "Parsed recent SQL learning interactions."

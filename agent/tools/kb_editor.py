import os
import logging
from abc import ABC
from typing import Optional, List, Dict, Any
import json

import requests

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.utils.api_utils import timeout


logger = logging.getLogger(__name__)


class KBEditorParam(ToolParamBase):
    """Parameters for knowledge base editing operations.

    Supports safe, explicit actions only. If direct chunk update is not supported
    by the server, the tool will emulate edits by appending a corrected chunk and
    optionally deleting the old chunk when possible.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "kb_editor",
            "description": "Edit KB documents or chunks: append chunk, replace document, delete document, delete chunk. Supports dataset by name or id and ingestion settings.",
            "parameters": {
                "action": {"type": "string", "description": "Operation to perform (append_chunk|replace_document|delete_document|delete_chunk)", "default": "append_chunk", "required": True},
                "dataset": {"type": "string", "description": "Target dataset id or name (supports variable reference)", "default": "", "required": True},
                "document_id": {"type": "string", "description": "Target document id (if applicable)", "default": "", "required": False},
                "document_name": {"type": "string", "description": "Target document name (optional, used to resolve id)", "default": "", "required": False},
                "chunk_id": {"type": "string", "description": "Target chunk id (for delete_chunk)", "default": "", "required": False},
                "content": {"type": "string", "description": "Content used for append_chunk or replace_document", "default": "", "required": False},
                "filename": {"type": "string", "description": "Logical filename for uploads (e.g., update.md)", "default": "update.md", "required": False},
                "mime": {"type": "string", "description": "MIME type for uploads", "default": "text/markdown", "required": False},
                "chunk_method": {"type": "string", "description": "Chunking method for ingestion (naive|manual|qa|table|paper|book|laws|presentation|picture|one|knowledge_graph|email|tag)", "default": "", "required": False},
                "parser_config": {"type": "string", "description": "Optional parser config JSON string to override defaults", "default": "", "required": False},
                "parse": {"type": "bool", "description": "Whether to start parsing after upload/replace", "default": True, "required": False},
                "enabled": {"type": "bool", "description": "Set document enabled status after update", "default": True, "required": False},
                "important_keywords": {"type": "string", "description": "Comma-separated important keywords when appending a chunk", "default": "", "required": False},
                "questions": {"type": "string", "description": "Optional newline-separated related questions for the chunk", "default": "", "required": False},
            },
        }
        super().__init__()
        self.action = "append_chunk"
        self.dataset = ""
        self.document_id: str = ""
        self.document_name: str = ""
        self.chunk_id: str = ""
        self.content: str = ""
        self.filename: str = "update.md"
        self.mime: str = "text/markdown"
        # ingestion tuning
        self.chunk_method: str = ""
        self.parser_config: str = ""
        self.parse: bool = True
        self.enabled: bool = True
        # append chunk enrichments
        self.important_keywords: str = ""
        self.questions: str = ""

    def check(self):
        if not self.action:
            raise ValueError("action is required")
        # Accept legacy dataset_id if present
        legacy_dataset_id = getattr(self, "dataset_id", "")
        if not self.dataset and not legacy_dataset_id:
            raise ValueError("dataset is required (id or name)")
        if self.action in ("replace_document", "append_chunk") and not self.content:
            raise ValueError("content is required for this action")
        if self.action in ("replace_document", "delete_document", "append_chunk", "delete_chunk") and not (self.document_id or self.action == "replace_document"):
            # For replace_document we can delete and recreate if document_id is provided; otherwise just create new
            pass


class KBEditor(ToolBase, ABC):
    component_name = "KBEditor"

    @timeout(os.environ.get("COMPONENT_EXEC_TIMEOUT", 45))
    def _invoke(self, **kwargs):
        # Sanitize and persist inputs into component inputs for event serialization
        from functools import partial as _partial
        def _san(v):
            if isinstance(v, _partial):
                return "<stream>"
            if isinstance(v, dict):
                return {k: _san(x) for k, x in v.items()}
            if isinstance(v, (list, tuple, set)):
                t = type(v)
                return t(_san(x) for x in v)
            return v
        for k, v in kwargs.items():
            self.set_input_value(k, _san(v))

        base = os.environ.get("RAGFLOW_BASE_URL", "http://127.0.0.1:9380")
        api_key = os.environ.get("RAGFLOW_API_KEY", "")
        if not api_key:
            raise RuntimeError("RAGFLOW_API_KEY not configured; cannot edit KB.")

        action = (kwargs.get("action") or self._param.action).strip()
        dataset = (kwargs.get("dataset") or kwargs.get("dataset_id") or self._param.dataset or getattr(self._param, "dataset_id", "")).strip()
        document_id = (kwargs.get("document_id") or self._param.document_id).strip()
        document_name = (kwargs.get("document_name") or self._param.document_name).strip()
        chunk_id = (kwargs.get("chunk_id") or self._param.chunk_id).strip()
        # Resolve streamed content before acting
        raw_content = kwargs.get("content") if kwargs.get("content") is not None else self._param.content
        if isinstance(raw_content, _partial):
            buf = ""
            for t in raw_content():
                if not t:
                    continue
                buf += t
            content = buf
        else:
            content = raw_content
        filename = (kwargs.get("filename") or self._param.filename).strip() or "update.md"
        mime = (kwargs.get("mime") or self._param.mime).strip() or "text/markdown"
        chunk_method = (kwargs.get("chunk_method") or self._param.chunk_method).strip()
        parser_config_str = (kwargs.get("parser_config") or self._param.parser_config).strip()
        parse_after = bool(self._param.parse if kwargs.get("parse") is None else kwargs.get("parse"))
        enabled = bool(self._param.enabled if kwargs.get("enabled") is None else kwargs.get("enabled"))
        important_keywords_str = (kwargs.get("important_keywords") or self._param.important_keywords).strip()
        questions_str = (kwargs.get("questions") or self._param.questions).strip()

        # Resolve dataset to id (accept name or id, or canvas variable reference like {begin@dataset})
        def resolve_dataset_id(value: str) -> str:
            from api.db.services.knowledgebase_service import KnowledgebaseService
            val = value
            if "@" in value:
                try:
                    val = self._canvas.get_variable_value(value)
                except Exception:
                    pass
            if not isinstance(val, str):
                if isinstance(val, (list, tuple)):
                    val = next((str(x) for x in val if str(x).strip()), "")
                else:
                    val = str(val)
            ok, kb = KnowledgebaseService.get_by_id(val)
            if ok:
                return kb.id
            ok, kb = KnowledgebaseService.get_by_name(val, self._canvas.get_tenant_id())
            if ok:
                return kb.id
            return value

        dataset_id = resolve_dataset_id(dataset)
        if not dataset_id:
            raise ValueError("Unable to resolve dataset id from 'dataset'")

        session = requests.Session()
        headers = {"Authorization": f"Bearer {api_key}"}

        def upload_document_text(ds_id: str, text: str, name: str, mime_type: str) -> str:
            files = {"file": (name, text.encode("utf-8"), mime_type)}
            r = session.post(f"{base}/api/v1/datasets/{ds_id}/documents", headers=headers, files=files, timeout=30)
            r.raise_for_status()
            return r.json()["data"][0]["id"]

        def append_chunk(ds_id: str, doc_id: str, text: str, important_keywords: Optional[List[str]] = None, questions: Optional[List[str]] = None):
            r = session.post(
                f"{base}/api/v1/datasets/{ds_id}/documents/{doc_id}/chunks",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "content": text,
                    **({"important_keywords": important_keywords} if important_keywords else {}),
                    **({"questions": questions} if questions else {}),
                },
                timeout=30,
            )
            r.raise_for_status()

        def delete_document(ds_id: str, doc_id: str):
            r = session.delete(
                f"{base}/api/v1/datasets/{ds_id}/documents",
                headers={**headers, "Content-Type": "application/json"},
                json={"ids": [doc_id]},
                timeout=30,
            )
            r.raise_for_status()

        def delete_chunk(ds_id: str, doc_id: str, chk_id: str) -> bool:
            r = session.delete(
                f"{base}/api/v1/datasets/{ds_id}/documents/{doc_id}/chunks",
                headers={**headers, "Content-Type": "application/json"},
                json={"chunk_ids": [chk_id]},
                timeout=20,
            )
            return r.status_code == 200

        def update_document_settings(ds_id: str, doc_id: str, chunk_method: str, parser_config: Optional[Dict[str, Any]], enabled: Optional[bool]):
            body: Dict[str, Any] = {}
            if chunk_method:
                body["chunk_method"] = chunk_method
            if parser_config is not None:
                body["parser_config"] = parser_config
            if enabled is not None:
                body["enabled"] = enabled
            if not body:
                return
            r = session.put(
                f"{base}/api/v1/datasets/{ds_id}/documents/{doc_id}",
                headers={**headers, "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            r.raise_for_status()

        def trigger_parse(ds_id: str, doc_id: str):
            r = session.post(
                f"{base}/api/v1/datasets/{ds_id}/chunks",
                headers={**headers, "Content-Type": "application/json"},
                json={"document_ids": [doc_id]},
                timeout=30,
            )
            r.raise_for_status()

        def resolve_document_id_by_name(ds_id: str, name: str) -> Optional[str]:
            if not name:
                return None
            r = session.get(
                f"{base}/api/v1/datasets/{ds_id}/documents",
                headers=headers,
                params={"name": name, "page_size": 1},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            docs = data.get("docs") if isinstance(data, dict) else data
            if isinstance(docs, list) and docs:
                return docs[0].get("id")
            return None

        # Dispatch actions
        if action == "append_chunk":
            if not document_id and document_name:
                document_id = resolve_document_id_by_name(dataset_id, document_name) or ""
            if not document_id:
                # Create new document first, then append (the initial content is already the chunk)
                document_id = upload_document_text(dataset_id, content, filename, mime)
                self.set_output("message", f"created_document:{document_id}")
            else:
                important_keywords = [s.strip() for s in important_keywords_str.split(",") if s.strip()] if important_keywords_str else None
                questions = [s.strip() for s in questions_str.splitlines() if s.strip()] if questions_str else None
                append_chunk(dataset_id, document_id, content, important_keywords, questions)
                self.set_output("message", f"appended_chunk_to:{document_id}")
            return "ok"

        if action == "replace_document":
            # If document_id provided, delete it first, then upload new content as a new document
            if document_id:
                try:
                    delete_document(dataset_id, document_id)
                except Exception as e:
                    logger.warning(f"Failed to delete doc {document_id} before replace: {e}")
            new_id = upload_document_text(dataset_id, content, filename, mime)
            # Apply optional ingestion settings
            parser_cfg: Optional[Dict[str, Any]] = None
            if parser_config_str:
                try:
                    parser_cfg = json.loads(parser_config_str)
                except Exception as e:
                    logger.warning(f"Invalid parser_config JSON: {e}")
            try:
                update_document_settings(dataset_id, new_id, chunk_method, parser_cfg, enabled)
            except Exception as e:
                logger.warning(f"Failed to update document settings for {new_id}: {e}")
            if parse_after:
                try:
                    trigger_parse(dataset_id, new_id)
                except Exception as e:
                    logger.warning(f"Failed to trigger parse for {new_id}: {e}")
            self.set_output("message", f"replaced_document:{document_id or 'n/a'}->new:{new_id}")
            self.set_output("document_id", new_id)
            return new_id

        if action == "delete_document":
            if not document_id:
                raise ValueError("document_id is required for delete_document")
            delete_document(dataset_id, document_id)
            self.set_output("message", f"deleted_document:{document_id}")
            return "ok"

        if action == "delete_chunk":
            if not (document_id and chunk_id):
                raise ValueError("document_id and chunk_id are required for delete_chunk")
            ok = delete_chunk(dataset_id, document_id, chunk_id)
            self.set_output("message", f"delete_chunk:{chunk_id}:{'ok' if ok else 'failed'}")
            return "ok" if ok else "failed"

        raise ValueError(f"Unsupported action: {action}")

    def thoughts(self) -> str:
        return "Editing KB content per request."

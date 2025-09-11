import re
import os
from abc import ABC
from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.utils.api_utils import timeout


class SectionExtractParam(ToolParamBase):
    def __init__(self):
        self.meta: ToolMeta = {
            "name": "section_extract",
            "description": "Extract a named markdown section from text (by heading).",
            "parameters": {
                "text": {"type": "string", "description": "Full markdown text.", "default": "", "required": True},
                "heading": {"type": "string", "description": "Section heading to extract (exact match without #).", "default": "Q→SQL Examples", "required": True},
                "level": {"type": "integer", "description": "Heading level (number of #).", "default": 2, "required": False},
                "include_heading": {"type": "bool", "description": "Whether to include the heading line in output.", "default": True, "required": False}
            }
        }
        super().__init__()
        self.text = ""
        self.heading = "Q→SQL Examples"
        self.level = 2
        self.include_heading = True

    def check(self):
        if not self.text or not isinstance(self.text, str):
            raise ValueError("text must be a non-empty string")
        if not self.heading:
            raise ValueError("heading is required")


class SectionExtract(ToolBase, ABC):
    component_name = "SectionExtract"

    @timeout(os.environ.get("COMPONENT_EXEC_TIMEOUT", 15))
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

        # Resolve possible streaming inputs to concrete strings before processing
        raw_text = kwargs.get("text", None)
        if isinstance(raw_text, _partial):
            buf = ""
            for t in raw_text():
                if not t:
                    continue
                buf += t
            text = buf
        else:
            text = raw_text if raw_text is not None else self._param.text
        heading = kwargs.get("heading") or self._param.heading
        level = int(kwargs.get("level") or self._param.level)
        include_heading = bool(kwargs.get("include_heading") if kwargs.get("include_heading") is not None else self._param.include_heading)

        # Build regex to capture from heading to next same-or-higher level or end
        hprefix = "#" * level
        # Match heading line ignoring leading/trailing spaces
        pattern = rf"(?m)^\s*{re.escape(hprefix)}\s+{re.escape(heading)}\s*\n([\s\S]*?)(?=^\s*#{{1,{level}}}\s+|\Z)"
        m = re.search(pattern, text)
        if not m:
            # Try any level
            pattern2 = rf"(?m)^\s*#+\s+{re.escape(heading)}\s*\n([\s\S]*?)(?=^\s*#+\s+|\Z)"
            m = re.search(pattern2, text)
        if not m:
            self.set_output("content", "")
            return ""
        body = m.group(1)
        out = (f"{hprefix} {heading}\n" + body) if include_heading else body
        self.set_output("content", out.strip())
        return out.strip()

    def thoughts(self) -> str:
        return "Extracted requested markdown section."

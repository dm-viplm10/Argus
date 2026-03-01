"""Prompt registry — central access to all agent prompt templates.

Mirrors the LLMRegistry pattern: task_name -> template, with .format() for templating.
"""

from __future__ import annotations

from src.agent.prompts.phase_strategist import PHASE_STRATEGIST_SYSTEM_PROMPT
from src.agent.prompts.planner import PLANNER_SYSTEM_PROMPT
from src.agent.prompts.query_refiner import QUERY_REFINER_PROMPT
from src.agent.prompts.risk_assessor import RISK_ASSESSOR_SYSTEM_PROMPT
from src.agent.prompts.search_and_analyze import SEARCH_ANALYZE_SYSTEM_PROMPT
from src.agent.prompts.supervisor import SUPERVISOR_SYSTEM_PROMPT
from src.agent.prompts.synthesizer import SYNTHESIZER_SYSTEM_PROMPT
from src.agent.prompts.verifier import VERIFIER_SYSTEM_PROMPT

PROMPT_TEMPLATES: dict[str, str] = {
    "supervisor": SUPERVISOR_SYSTEM_PROMPT,
    "planner": PLANNER_SYSTEM_PROMPT,
    "phase_strategist": PHASE_STRATEGIST_SYSTEM_PROMPT,
    "query_refiner": QUERY_REFINER_PROMPT,
    "search_and_analyze": SEARCH_ANALYZE_SYSTEM_PROMPT,
    "verifier": VERIFIER_SYSTEM_PROMPT,
    "risk_assessor": RISK_ASSESSOR_SYSTEM_PROMPT,
    "synthesizer": SYNTHESIZER_SYSTEM_PROMPT,
}


class PromptRegistry:
    """Registry for agent prompt templates. Use get_prompt(task, **kwargs) to format."""

    def get_prompt(self, task: str, **kwargs: object) -> str:
        """Return the formatted prompt for the given task."""
        template = PROMPT_TEMPLATES.get(task)
        if template is None:
            raise KeyError(f"No prompt registered for task '{task}'")
        return template.format(**kwargs)

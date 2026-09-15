"""Note generation via LLM in the fixed format of docs/CONCEPT.md, section 5,
with anti-hallucination post-checks (docs/CONCEPT.md, sections 6.4 and 10.2).

Implemented: OpenAI-compatible client (``llm``), prompt builders (``prompt``),
format validation (``notes``) and the one-cycle runner (``pipeline``).
"""

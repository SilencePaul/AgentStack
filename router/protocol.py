"""Recognize output-contract failures without interpreting text as tool calls."""
import re


def simulated_tool_markup(text):
    """Match a tool-call block with a function tag, not ordinary XML or prose."""
    return bool(re.search(
        r'<tool_call\b[^>]*>\s*<function(?:\s|=|>)', text, re.I
    ))

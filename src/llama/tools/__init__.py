"""Tools available to Atreus.

Modules here are discovered at runtime by llama.load_tools(), so nothing needs
to be imported or listed in this file -- adding a module is enough. Each module
exposes one function named after it, with a docstring and type hints, since
ollama derives the tool schema from those.
"""

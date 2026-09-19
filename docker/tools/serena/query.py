#!/usr/bin/env python3
"""One definition lookup and one reference lookup through Serena's own tools.

    query.py <project-dir> <symbol>

Runs find_symbol (the name as a name-path pattern, whole project) and, for the
first definition found, find_referencing_symbols, the way an agent would call
them over MCP. Prints the two tool results (JSON) to stdout.
"""
import json
import sys

from serena.agent import SerenaAgent
from serena.config.serena_config import LanguageBackend, SerenaConfig
from serena.tools import FindReferencingSymbolsTool, FindSymbolTool


def main():
    project, symbol = sys.argv[1], sys.argv[2]
    config = SerenaConfig.from_config_file()
    config.web_dashboard = False
    config.gui_log_window = False
    config.language_backend = LanguageBackend.LSP
    agent = SerenaAgent(project=project, serena_config=config)
    try:
        find_symbol = agent.get_tool(FindSymbolTool)
        find_refs = agent.get_tool(FindReferencingSymbolsTool)
        defs = agent.execute_task(lambda: find_symbol.apply(name_path_pattern=symbol, include_info=False))
        print(f"## find_symbol {symbol} in {project}")
        print(defs)
        try:
            found = json.loads(defs)
        except ValueError:
            found = []
        if isinstance(found, list) and found:
            first = found[0]
            name_path, rel = first.get("name_path", symbol), first.get("relative_path", "")
            refs = agent.execute_task(lambda: find_refs.apply(name_path=name_path, relative_path=rel))
            print(f"## find_referencing_symbols {name_path} {rel}")
            print(refs)
    finally:
        # on_shutdown stops the language servers; shutdown() would also SIGTERM this process.
        agent.on_shutdown()


if __name__ == "__main__":
    main()

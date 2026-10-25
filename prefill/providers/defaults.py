"""The providers that ship with the app. Your changes live in data/config.json, never here."""

DISALLOWED_TOOLS = "Bash Read Write Edit Glob Grep WebFetch WebSearch NotebookEdit Task"

BUILTIN_PROVIDERS: dict[str, dict] = {
    "claude": {  # the free Claude Code wrapper
        "type": "cli",
        "command": [
            "fcc-claude",
            "-p",
            "--output-format",
            "json",
            "--model",
            "{model}",
            "--safe-mode",
            "--disallowedTools",
            DISALLOWED_TOOLS,
        ],
        "model": "sonnet",
        "system_prompt_flag": "--system-prompt",
        "prompt_via": "stdin",
        "envelope_key": "result",
        "timeout": 300,
    },
    "agy": {  # Antigravity CLI
        "type": "cli",
        "command": ["agy", "-p", "{prompt}", "--print-timeout", "5m"],
        "prompt_via": "arg",  # no system-prompt flag: system and user prompts go in as one text
        "timeout": 330,  # a little longer than agy's own 5m limit
    },
}

DEFAULT_PROVIDER_NAME = "claude"

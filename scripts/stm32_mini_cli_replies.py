"""Register equivalent Mini CLI reply copies before objects are constructed."""

Import("env")

if env["PIOENV"] == "wio-e5-mini_repeater":
    import importlib.util
    from pathlib import Path

    source = Path(env.subst("$PROJECT_DIR/scripts/nrf52_flash_trim.py")).resolve()
    spec = importlib.util.spec_from_file_location("mesh_mini_cli_replies", source)
    replies = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replies)
    env.AddBuildMiddleware(replies.compile_shared_cli_replies, "*CommonCLI.cpp")

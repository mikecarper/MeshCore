"""Register equivalent Mini CLI reply copies before objects are constructed."""

Import("env")

if env["PIOENV"] == "wio-e5-mini_repeater":
    import importlib.util
    from pathlib import Path

    source = Path(env.subst("$PROJECT_DIR/scripts/nrf52_flash_trim.py")).resolve()
    spec = importlib.util.spec_from_file_location("mesh_mini_cli_replies", source)
    replies = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replies)
    prefix_path = Path(env.subst("$PROJECT_DIR/scripts/nrf52_cli_prefix_trim.py")).resolve()
    prefix_spec = importlib.util.spec_from_file_location("mesh_mini_cli_prefix", prefix_path)
    prefix = importlib.util.module_from_spec(prefix_spec)
    prefix_spec.loader.exec_module(prefix)
    prefix.install(env)
    lifetime_path = Path(env.subst("$PROJECT_DIR/scripts/stm32_mini_program_lifetime.py")).resolve()
    lifetime_spec = importlib.util.spec_from_file_location("mesh_mini_lifetime", lifetime_path)
    lifetime = importlib.util.module_from_spec(lifetime_spec)
    lifetime_spec.loader.exec_module(lifetime)
    lifetime.install(env, replies)
    env.AddBuildMiddleware(replies.compile_shared_cli_replies, "*CommonCLI.cpp")

import plistlib
import sys
from pathlib import Path


def generate_launch_agent(
    project_directory: str,
    config_path: str,
    output_path: str,
    python_executable: str = "",
) -> str:
    project = Path(project_directory).resolve()
    config = Path(config_path).resolve()
    output = Path(output_path).resolve()
    logs = project / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    executable = str(Path(python_executable or sys.executable).resolve())
    payload = {
        "Label": "com.pokemon-card-target.monitor",
        "ProgramArguments": [
            executable,
            "-m",
            "pokemon_bot",
            "--config",
            str(config),
        ],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "monitor.log"),
        "StandardErrorPath": str(logs / "monitor.error.log"),
    }
    with output.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return str(output)

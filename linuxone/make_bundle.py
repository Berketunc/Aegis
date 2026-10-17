#!/usr/bin/env python3
"""Package reviewable project files for transfer to the existing SSH session."""
from pathlib import Path
import hashlib
import subprocess
import tarfile


def main():
    project = Path(__file__).resolve().parent.parent
    output = project / ".deliverables" / "aegis-person-b.tar.gz"
    output.parent.mkdir(exist_ok=True)
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=project, check=True, capture_output=True,
    )
    paths = sorted(set(path for path in result.stdout.decode().split("\0") if path))
    with tarfile.open(output, "w:gz") as archive:
        for name in paths:
            source = project / name
            if source.is_file() and not source.is_symlink():
                archive.add(source, arcname=name, recursive=False)
    print(output)
    print("SHA-256:", hashlib.sha256(output.read_bytes()).hexdigest())
    print("Files:", len(paths))


if __name__ == "__main__":
    main()

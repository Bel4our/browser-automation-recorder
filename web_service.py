import subprocess
import sys

if __name__ == "__main__":
    try:
        subprocess.run(["node", "web_service.js"], check=True)
    except KeyboardInterrupt:
        sys.exit(0)
    except FileNotFoundError:
        print("Error: Node.js is not installed or not in PATH", file=sys.stderr)
        sys.exit(1)
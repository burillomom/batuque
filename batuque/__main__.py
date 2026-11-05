"""
Entrypoint for Batuque.
Launches the GTK4/Libadwaita GUI by default, or executes CLI subcommands if provided.
"""
import sys


CLI_COMMANDS = ("scan", "stats", "nas", "devices", "sync", "--help", "-h", "--version", "-v")


def run_entrypoint():
    if len(sys.argv) > 1 and sys.argv[1] in CLI_COMMANDS:
        from batuque.cli import main
        return main()
    else:
        try:
            from batuque.ui.app import run_app
            return run_app()
        except Exception as e:
            print(f"Erro ao iniciar interface gráfica: {e}. Exibindo ajuda CLI:")
            from batuque.cli import main
            return main()


if __name__ == "__main__":
    sys.exit(run_entrypoint() or 0)

"""
Entrypoint for SoundBee.
"""
import sys

if len(sys.argv) > 1 and sys.argv[1] in ("scan", "stats", "nas", "devices", "sync", "--help", "-h"):
    from soundbee.cli import main
    main()
else:
    # Launch GUI
    try:
        from soundbee.ui.app import run_app
        run_app()
    except Exception as e:
        print(f"Error launching GUI: {e}. Falling back to CLI help.")
        from soundbee.cli import main
        main()

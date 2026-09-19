"""PyInstaller entry script for mineai.exe."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from mineai.__main__ import main
    main()

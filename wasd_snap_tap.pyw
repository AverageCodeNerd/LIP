"""Launch the Tkinter app with pythonw so no console window is shown."""

import os
import runpy


app_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wasd_snap_tap.py")
runpy.run_path(app_script, run_name="__main__")

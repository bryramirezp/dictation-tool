"""A fullscreen sheet of one colour, for tools/screenshots.py to shoot against.

    py -3 tools/_backdrop.py 000000

Not useful on its own. The screenshot script photographs the app twice, once
over black and once over white, and solves for the transparency from the
difference -- so it needs something that is exactly the colour it claims to be,
covering everything the app might sit in front of.
"""
import sys
import tkinter as tk

colour = "#" + (sys.argv[1] if len(sys.argv) > 1 else "000000")

root = tk.Tk()
root.overrideredirect(True)
# Deliberately NOT topmost. The app is, and Windows keeps every topmost window
# above every ordinary one, so this covers the desktop and nothing else without
# anyone having to fight over z-order. Making this topmost too put it over the
# app in both shots, and the matte came out empty.
root.configure(bg=colour)
root.geometry("%dx%d+0+0" % (root.winfo_screenwidth(), root.winfo_screenheight()))
# No cursor over it, in case the pointer strays into a shot.
root.configure(cursor="none")
root.mainloop()

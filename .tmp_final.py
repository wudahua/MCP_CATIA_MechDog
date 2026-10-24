import pathlib

# ---- 1) pocket: symmetric with one empty side keeps the side that cuts ----
p = pathlib.Path("catia_mcp/tools/part_design.py")
s = p.read_text(encoding="utf-8")
old = '''        explicit_direction = "direction" in args or symmetric
        flipped = False
        if took is False and not explicit_direction:'''
new = '''        explicit_direction = "direction" in args or symmetric
        flipped = False
        single_sided = False
        if took is False and symmetric:
            # A mid-plane cut where one half is air: the primary side removed
            # nothing, but the other side has material. Honor the half that
            # exists and say so - failing the whole feature (which is what
            # happened to the reporter) leaves a 2-feature hole to patch by hand.
            pocket.DirectionOrientation = 0 if reverse else 1
            retry_error = self._try_update(part, pocket)
            after, retook = self._settle(part, body, before, expect="subtract")
            if retook:
                flipped = True
                took = True
                single_sided = True
                update_error = retry_error
        if took is False and not explicit_direction:'''
assert old in s
s = s.replace(old, new, 1)

s = s.replace('''        mirror = None
        if symmetric:''', '''        mirror = None
        if symmetric and not single_sided:''', 1)

s = s.replace('''            flip_note = " (direction auto-flipped to reach material)" if flipped else ""''',
'''            flip_note = " (direction auto-flipped to reach material)" if flipped else ""
            if single_sided:
                flip_note = (" (only one side of the sketch plane had material, so "
                             "this is a single-sided cut despite symmetric=true)")''')
p.write_text(s, encoding="utf-8", newline="\n")
print("pocket single-sided symmetric")

# ---- 2) create_sketch: plane -> world axis table in the description ----
p = pathlib.Path("catia_mcp/tools/sketcher.py")
s = p.read_text(encoding="utf-8")
old = '''                    "Create a new 2D sketch and open it for editing. The support "
                    "is a main datum plane ('xy', 'yz', 'zx'), optionally offset "'''
new = '''                    "Create a new 2D sketch and open it for editing. The reply names "
                    "the axes, because they are NOT the intuitive ones: xy is "
                    "H = X / V = Y / normal = Z, yz is H = Y / V = Z / normal = X, and "
                    "zx is H = Z / V = X / normal = Y. So a shaft revolved about the "
                    "sketch's V axis spins about world Y, Z and X respectively; put a "
                    "revolved part on the plane whose V axis is the world axis the "
                    "spec names. Note also that CATIA's 'top' plane is zx (normal Y) "
                    "while SolidWorks' Top is XZ - the same words point at different "
                    "planes. The support "
                    "is a main datum plane ('xy', 'yz', 'zx'), optionally offset "'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8", newline="\n")
print("create_sketch axis table")

# ---- 3) close_document: verify the close actually happened ----
p = pathlib.Path("catia_mcp/tools/document.py")
s = p.read_text(encoding="utf-8")
old = '''        try:
            doc.Close()
        except Exception as e:'''
new = '''        before = self._document_names()
        try:
            doc.Close()
        except Exception as e:'''
assert old in s
s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8", newline="\n")
print("close_document: names snapshot added")

from langchain_core.tools import tool

_OPENSCAD_REFERENCE = {
    "cylinder": (
        "cylinder(h, r|d, r1|d1, r2|d2, center);\n"
        "  - There is NO cone() function. Use cylinder(h=10, r1=5, r2=0) for a cone.\n"
        "  - r = radius, d = diameter. r1/r2 for different top/bottom radii.\n"
        "  - center=true centers the cylinder on the origin vertically."
    ),
    "sphere": "sphere(r|d);\n  - r = radius, d = diameter.",
    "cube": (
        "cube(size, center);\n"
        "  - size can be a scalar or [x,y,z] vector.\n"
        "  - center=true centers the cube on the origin."
    ),
    "difference": (
        "difference() { base_shape(); subtracted_shape(); }\n"
        "  - Use eps=0.01 offset to avoid z-fighting: translate([0,0,-eps])\n"
        "  - First child is the base; all subsequent children are subtracted."
    ),
    "union": "union() { shape1(); shape2(); }\n  - Combines multiple shapes into one.",
    "intersection": (
        "intersection() { shape1(); shape2(); }\n"
        "  - Keeps only the overlapping volume."
    ),
    "linear_extrude": (
        "linear_extrude(height, center, twist, slices, scale)\n"
        "  - Extrudes a 2D shape along the Z axis.\n"
        "  - twist = rotation in degrees over the extrusion height."
    ),
    "rotate_extrude": (
        "rotate_extrude(angle, $fn)\n"
        "  - Rotates a 2D shape around the Z axis.\n"
        "  - The 2D shape must be in the positive X half-plane."
    ),
    "hull": (
        "hull() { shape1(); shape2(); }\n"
        "  - Creates the convex hull of child shapes."
    ),
    "minkowski": (
        "minkowski() { base(); tool(); }\n"
        "  - Adds the 'tool' shape to every point of 'base' (rounded edges).\n"
        "  - WARNING: Very slow. Use $fn=32 or lower."
    ),
    "translate": (
        "translate([x, y, z]) shape();\n"
        "  - Moves child shape by the given vector."
    ),
    "rotate": (
        "rotate([x, y, z]) shape(); OR rotate(a, v) shape();\n"
        "  - Angles in degrees. Applied in order: Z, Y, X (when using vector form)."
    ),
    "mirror": (
        "mirror([x, y, z]) shape();\n"
        "  - Mirrors across the plane defined by the normal vector.\n"
        "  - mirror([0,1,0]) mirrors across the XZ plane (left-right)."
    ),
    "polygon": (
        "polygon(points, paths);\n"
        "  - points = list of [x,y] coordinates.\n"
        "  - paths = optional list of point indices defining the polygon outline."
    ),
    "for_loop": (
        "for (i = [start:step:end]) { ... }\n"
        "  - OpenSCAD uses ':' not ',' in range expressions.\n"
        "  - for (i = [0:1:5]) creates i = 0, 1, 2, 3, 4, 5."
    ),
    "module": (
        "module name(param1, param2=default) { ... }\n"
        "  - Always call modules explicitly: name();\n"
        "  - Modules cannot return values; use functions for that."
    ),
    "import": (
        'import("filename.stl");\n'
        "  - Imports STL, OFF, AMF, 3MF, DXF, SVG files.\n"
        "  - Use surface() for height maps."
    ),
    "color": (
        'color("red") shape(); OR color([r,g,b,a]) shape();\n'
        "  - Named colors: red, green, blue, yellow, cyan, magenta, etc.\n"
        "  - RGBA values are 0.0 to 1.0."
    ),
    "scale": (
        "scale([x, y, z]) shape();\n"
        "  - Scales child shape by the given factors."
    ),
    "resize": (
        "resize([x, y, z], auto) shape();\n"
        "  - Resizes to exact dimensions. auto=true scales other axes proportionally."
    ),
    "text": (
        'text("string", size, font, halign, valign);\n'
        "  - 2D text, must be used with linear_extrude for 3D.\n"
        '  - halign: "left", "center", "right". valign: "top", "center", "bottom".'
    ),
}


@tool
def search_openscad_reference(query: str) -> str:
    """Search the OpenSCAD documentation for correct syntax, usage, and best practices.
    Provide a keyword or topic like 'cylinder', 'difference', 'for_loop', 'mirror', etc.
    Use this when you are unsure about OpenSCAD syntax or want to verify correct usage."""

    query_lower = query.lower().strip()

    # Direct match
    if query_lower in _OPENSCAD_REFERENCE:
        return _OPENSCAD_REFERENCE[query_lower]

    # Fuzzy match — search all entries for the query term
    matches = []
    for key, doc in _OPENSCAD_REFERENCE.items():
        if query_lower in key or query_lower in doc.lower():
            matches.append(f"## {key}\n{doc}")

    if matches:
        return "\n\n".join(matches)

    return (
        f"No documentation found for '{query}'. "
        f"Available topics: {', '.join(sorted(_OPENSCAD_REFERENCE.keys()))}. "
        "Try a more specific keyword."
    )

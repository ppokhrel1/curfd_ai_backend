import cadquery as cq

# Parameters
length = 10.0
width = 10.0
thickness = 10.0
center_hole_dia = 5.0

# Construction
result = (
    cq.Workplane("XY")
    .box(length, width, thickness)
    .faces(">Z")
    .workplane()
    .hole(center_hole_dia)
)

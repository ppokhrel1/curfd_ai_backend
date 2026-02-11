import math

import cadquery as cq
preserved_solids = []

# Component 1: Flight controller
def generate_flight_controller():
    # Workplane on XY to define base shape
    workplane = cq.Workplane("XY")

    # Define a rectangular prism representing the flight controller body
    # Dimensions: 80mm x 70mm x 3mm thickness (for arm and housing)
    # Using standard dimensions based on typical drone designs

    # Base rectangle for main housing
    rect_housing = workplane.box(90, 70, 3)

    # Add mounting points or features using polyline
    # For simplicity, we'll use a basic rectangular frame
    # The actual feature is defined by creating a closed loop around the edges

    # Top face of the housing
    top_face = rect_housing.copy().translate((0, 0, 3))

    # Bottom face at Z=0
    bottom_face = rect_housing.copy().translate((0, 0, 0))

    # Create a vertical wall from X=0 to X=90, Y=0 to Y=70
    .faces(">Z").workplane()
    left_wall = workplane.moveTo(0, 0).lineTo(90, 0).lineTo(90, 70).lineTo(0, 70).close()

    # Right side wall
    .faces(">Z").workplane()
    right_wall = workplane.moveTo(90, 0).lineTo(90, 70).lineTo(0, 70).lineTo(0, 0).close()

    # Front panel
    .faces(">Z").workplane()
    front_panel = workplane.moveTo(0, 0).lineTo(90, 0).lineTo(90, 70).lineTo(0, 70).close()

    # Back panel
    .faces(">Z").workplane()
    back_panel = workplane.moveTo(0, 0).lineTo(90, 0).lineTo(90, 70).lineTo(0, 70).close()

    # Combine into one solid object
    # We will now build up the full assembly step-by-step

    # Start with a large box that represents the overall enclosure
    .faces(">Z").workplane()
    outer_box = workplane.moveTo(-45, -45).lineTo(45, -45).lineTo(45, 45).lineTo(-45, 45).close()

    # Now extrude down to form the chassis
    chassis = outer_box.extrude(3)

    # Cut out internal space for electronics
    .faces(">Z").workplane()
    inner_space = workplane.moveTo(0, 0).lineTo(90, 0).lineTo(90, 70).lineTo(0, 70).close()

    # Remove interior volume
    cutout = inner_space.cut(chassis, clean=True)

    # Finalize the model
    final_model = cutout.close()

    return final_model

# Component 2: Motor mount area
def generate_motor_mount():
    # This function creates a simple cylindrical motor mount
    # With specific dimensions matching the requirement
    workplane = cq.Workplane("XZ")

    # Outer diameter of the cylinder
    radius_outer = 10
    height_cylinder = 160

    # Create circular cross-section
    .faces(">Z").workplane()
    circle = workplane.circle(radius_outer)

    # Extrude along Z axis
    cylinder = circle.extrude(height_cylinder)

    # Apply material properties if needed
    # But since this is just a placeholder, keep it minimal

    return cylinder

# Component 3: Arm support structure
def generate_arm_support():
    # Generate three-dimensional arm supports
    # Each arm has specified length and width
    # Arms are made of 3mm thick material

    # First, define the base of each arm
    workplane = cq.Workplane("YZ")

    # Length of each arm segment
    arm_length = 160

    # Width of the arm section
    arm_width = 3

    # Build a series of connected segments forming an articulated arm
    # Starting from origin point
    start_point = (0, 0, 0)

    # Create first segment
    .faces(">Z").workplane()
    seg_1 = workplane.moveTo(start_point[0], start_point[1]).lineTo(
        start_point[0] + arm_length * 0.5,
        start_point[1]
    .faces(">Z").workplane()
    ).lineTo(
        start_point[0],
        start_point[1] + arm_width
    )

    # Second segment connecting to next joint
    .faces(">Z").workplane()
    seg_2 = workplane.moveTo(seg_1.end()[0], seg_1.end()[1])
    .faces(">Z").workplane()
    seg_2.lineTo(
        seg_1.start()[0] + arm_length * 0.5,
        seg_1.start()[1]
    )

    # Third segment continuing the path
    .faces(">Z").workplane()
    seg_3 = workplane.moveTo(seg_2.end()[0], seg_2.end()[1])
    .faces(">Z").workplane()
    seg_3.lineTo(
        seg_2.start()[0] + arm_length * 0.5,
        seg_2.start()[1]
    )

    # Close off the entire path
    seg_3.closePath()

    # Return the complete arm structure
    return seg_3

# Component 4: Main control unit
def generate_control_unit():
    # Control unit consists of two main sections:
    # 1. Central processing module
    # 2. Display screen

    # Working plane set to XYZ coordinates
    workplane = cq.Workplane("XYZ")

    # Define central processing block
    processor_block = workplane.rect(100, 80).extrude(10)

    # Define display screen
    .faces(">Z").workplane()
    screen_frame = workplane.rect(100, 80).extrude(10)

    # Join both blocks together
    combined = processor_block.union(screen_frame, clean=True)

    # Ensure proper closure
    combined.close()

    return combined

# Component 5: Battery pack
def generate_battery_pack():
    # Simple battery pack shaped like a cube
    # Made of 3mm thick aluminum alloy
    workplane = cq.Workplane("XYZ")

    # Cube dimensions: 100x100x100 units
    size = 100
    depth = 100

    # Create a cuboid
    battery_cube = workplane.box(size, size, depth)

    # Make sure it's properly closed
    battery_cube.close()

    return battery_cube

# Component 6: Sensor array
def generate_sensor_array():
    # Array of sensors arranged in grid pattern
    # Sensors mounted on flat surface
    workplane = cq.Workplane("XY")

    # Grid layout: 4 rows × 4 columns
    num_rows = 4
    num_cols = 4

    # Calculate spacing between sensor elements
    spacing_x = 20
    spacing_y = 20

    # Initialize list of sensor positions
    sensors = []

    # Loop through each row and column
    for i in range(num_rows):
        for j in range(num_cols):
            pos_x = i * spacing_x
            pos_y = j * spacing_y

            # Place sensor at position
            sensor = workplane.point(pos_x, pos_y)

            # Store sensor data
            sensors.append(sensor)

    # Connect them via lines
    .faces(">Z").workplane()
    connections = [sensor.lineTo(sensors[(i+1)%num_cols]) for i in range(len(sensors))]

    # Close connection loops
    connections[-1].toClose()

    # Return assembled sensor array
    return connections

# Assembly creation function
def create_assembly():
    # Design domain: Large enclosing box
    design_domain = cq.Workplane("XY").box(200, 200, 50)

    # Preserved solids: All individual components created above
    preserved_solids = [
        generate_flight_controller(),
        generate_motor_mount(),
        generate_arm_support(),
        generate_control_unit(),
        generate_battery_pack(),
        generate_sensor_array()
    ]

    # Union all preserved solids within the design domain
    result = design_domain
    for comp in preserved_solids:
        result = result.union(comp, clean=True)

    # Fix fixed faces
    result = result.faces("<Z").tag("FIXED")

    return result

result = create_assembly()
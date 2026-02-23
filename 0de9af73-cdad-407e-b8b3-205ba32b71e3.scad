// --- GLOBAL PARAMETERS ---
$fn = 60; // Facets for curves, higher = smoother
eps = 0.05; // Epsilon for preventing Z-fighting

// --- FUSELAGE PARAMETERS ---
fuselage_length = 80; // Length of the main cylindrical body section. Centered at X=0.
fuselage_radius = 10; // Radius of the main cylindrical body
nose_cone_length = 20; // Length of the front nose cone/dome
tail_cone_length = 30; // Length of the rear tapered tail section
tail_cone_radius_end = 2; // Radius at the very end of the tail cone

// --- MAIN WING PARAMETERS ---
main_wing_span = 120; // Total width from wingtip to wingtip
main_wing_chord = 30; // Front to back depth of the wing
main_wing_thickness = 3; // Thickness of the wing
main_wing_position_x = 0; // X-position along the fuselage where wings are centered (0 = mid-fuselage)
main_wing_dihedral_angle = 5; // Angle of main wings upwards from horizontal

// --- TAIL WING (HORIZONTAL STABILIZER) PARAMETERS ---
tail_wing_span = 40; // Total width of the horizontal stabilizer
tail_wing_chord = 15; // Front to back depth of the horizontal stabilizer
tail_wing_thickness = 2; // Thickness of the horizontal stabilizer
tail_wing_position_x = 65; // X-position along the fuselage, centered on tail cone
tail_wing_position_z = 2; // Z-position relative to fuselage center (positive is up)

// --- VERTICAL STABILIZER (TAIL FIN) PARAMETERS ---
tail_fin_height = 25; // Total height of the vertical tail fin
tail_fin_chord = 20; // Front to back depth of the vertical tail fin at its base
tail_fin_thickness = 2; // Thickness of the vertical tail fin
tail_fin_position_x = 65; // X-position along the fuselage, centered on tail cone

// --- ENGINE PARAMETERS ---
num_engines = 2; // Number of engines (typically 2 for wing-mounted)
engine_diameter = 12; // Diameter of the engine nacelle
engine_length = 25; // Length of the engine nacelle
engine_nacelle_offset_y = 30; // Y-offset from fuselage center for wing-mounted engines
engine_nacelle_offset_z = -8; // Z-offset from wing center (negative for below wing)
engine_nacelle_position_x = 8; // X-position under the wing, relative to fuselage center

// --- PROPELLER PARAMETERS (if a prop plane) ---
propeller_radius = 7; // Radius of the propeller blades
propeller_thickness = 1; // Thickness of propeller blades
num_propeller_blades = 3; // Number of blades per propeller

// --- LANDING GEAR PARAMETERS ---
wheel_diameter = 8; // Diameter of the landing gear wheels
wheel_thickness = 4; // Thickness of the landing gear wheels
strut_diameter = 2; // Diameter of the landing gear struts
strut_height = 15; // Height of the landing gear struts (from fuselage bottom to wheel center)

// Nose gear position
nose_gear_position_x = -54; // X-position, slightly behind the front tip of nose cone
nose_gear_offset_y = 0; // Y-position for nose gear (usually centered)

// Main gear positions (relative to main wing section)
main_gear_position_x = 8; // X-position under the wing, slightly forward
main_gear_offset_y = 40; // Y-spread for main landing gears

// --- MODULES ---

module create_fuselage() {
    // Main cylindrical body (extends from -fuselage_length/2 to +fuselage_length/2)
    cylinder(h = fuselage_length, r = fuselage_radius, center = true);

    // Nose cone/dome
    translate([-(fuselage_length / 2 + nose_cone_length / 2), 0, 0])
    rotate([0, 90, 0]) // Rotate to align cone along X
    cylinder(h = nose_cone_length, r1 = 0, r2 = fuselage_radius, center = true);

    // Tail cone
    translate([fuselage_length / 2 + tail_cone_length / 2, 0, 0])
    rotate([0, 90, 0]) // Rotate to align cone along X
    cylinder(h = tail_cone_length, r1 = fuselage_radius, r2 = tail_cone_radius_end, center = true);
}

module create_main_wing() {
    // Wing section, centered at origin for easy rotation and then positioning
    rotate([0, main_wing_dihedral_angle, 0]) { // Dihedral angle
        cube([main_wing_chord, main_wing_span, main_wing_thickness], center = true);
    }
}

module create_tail_wing() {
    // Horizontal stabilizer, centered at origin
    cube([tail_wing_chord, tail_wing_span, tail_wing_thickness], center = true);
}

module create_tail_fin() {
    // Vertical stabilizer, centered at origin (base for Z is 0)
    cube([tail_fin_chord, tail_fin_thickness, tail_fin_height], center = true);
}

module create_engine_nacelle() {
    // Engine nacelle, aligned along X-axis by default
    cylinder(h = engine_length, r = engine_diameter / 2, center = true);
}

module create_propeller() {
    // Propeller hub and blades
    union() {
        // Hub
        cylinder(h = propeller_thickness + eps, r = propeller_radius / 3, center = true);

        // Blades
        for (i = [0 : num_propeller_blades - 1]) {
            rotate([0, 0, i * (360 / num_propeller_blades)]) {
                translate([0, propeller_radius * 0.5, 0]) { // Blade starts from hub edge
                    cube([propeller_thickness, propeller_radius * 0.8, propeller_thickness * 0.5], center = true);
                }
            }
        }
    }
}

module create_landing_gear_wheel() {
    // Wheel, centered at origin, aligned along Z-axis by default
    cylinder(h = wheel_thickness, r = wheel_diameter / 2, center = true);
}

module create_landing_gear_strut() {
    // Strut, centered at origin, aligned along Z-axis by default
    cylinder(h = strut_height, r = strut_diameter / 2, center = true);
}

module main_assembly() {
    // Fuselage (main cylindrical part centered at X=0, Y=0, Z=0)
    color("lightgray")
    create_fuselage();

    // Main wings
    color("darkgray")
    translate([main_wing_position_x, 0, 0]) // Position along X relative to fuselage center
    create_main_wing();

    // Tail Wings (Horizontal Stabilizer)
    color("darkgray")
    translate([tail_wing_position_x, 0, fuselage_radius + tail_wing_position_z]) // Position relative to fuselage center and height
    create_tail_wing();

    // Vertical Tail Fin
    color("darkgray")
    translate([tail_fin_position_x, 0, fuselage_radius + tail_fin_height / 2]) // Position relative to fuselage center and on top
    create_tail_fin();

    // Engines and Propellers
    if (num_engines > 0) {
        for (i = [0 : num_engines - 1]) {
            side_multiplier = (i % 2 == 0) ? 1 : -1; // Alternate sides for left/right engines
            
            // Engine Nacelle
            color("darkslategray")
            translate([
                engine_nacelle_position_x,
                engine_nacelle_offset_y * side_multiplier,
                engine_nacelle_offset_z
            ])
            rotate([0, 90, 0]) // Align engine along X-axis (it's built along Z by default)
            create_engine_nacelle();

            // Propeller
            color("maroon")
            translate([
                engine_nacelle_position_x - engine_length / 2 - propeller_thickness / 2, // Place at the front of the engine
                engine_nacelle_offset_y * side_multiplier,
                engine_nacelle_offset_z
            ])
            rotate([90, 0, 0]) // Rotate propeller to face forward (it's built along Z by default)
            create_propeller();
        }
    }

    // Landing Gear
    // Nose Gear
    color("dimgray")
    translate([
        nose_gear_position_x,
        nose_gear_offset_y,
        -fuselage_radius - strut_height / 2 // Strut starts at bottom of fuselage
    ])
    create_landing_gear_strut();
    translate([
        nose_gear_position_x,
        nose_gear_offset_y,
        -fuselage_radius - strut_height - wheel_diameter / 2 // Wheel center below strut
    ])
    rotate([90, 0, 0]) // Rotate wheel to be vertical (it's built along Z by default)
    create_landing_gear_wheel();

    // Main Gears
    color("dimgray")
    for (i = [0 : 1]) { // Two main landing gears
        side_multiplier = (i == 0) ? 1 : -1;
        
        // Strut
        translate([
            main_gear_position_x,
            main_gear_offset_y * side_multiplier,
            -fuselage_radius - strut_height / 2
        ])
        create_landing_gear_strut();

        // Wheel
        translate([
            main_gear_position_x,
            main_gear_offset_y * side_multiplier,
            -fuselage_radius - strut_height - wheel_diameter / 2
        ])
        rotate([90, 0, 0]) // Rotate wheel to be vertical
        create_landing_gear_wheel();
    }
}

main_assembly();

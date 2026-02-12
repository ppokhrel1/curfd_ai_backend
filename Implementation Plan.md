# Implementation Plan

## Goal
Build a FastAPI backend to support user sessions, chat records, and a 3D generation pipeline aligned with `app_diagram.xml`.

## Phases
1. **Foundation**
   - Project layout, configuration, environment variables.
   - Core FastAPI app, health routes, and logging.

2. **Data Layer**
   - SQLAlchemy models + Alembic migrations.
   - Core entities: Users, Sessions, Chats, Messages, Assets, Jobs.

3. **API Layer**
   - Auth/session endpoints.
   - Chat and message endpoints.
   - Job orchestration endpoints for the 3D pipeline.

4. **Storage + Integrations**
   - Object storage (S3/MinIO) abstraction.
   - Vector DB and metadata DB stubs.

5. **Ops + DevX**
   - Docker, env templates, scripts.
   - Tests and local run instructions.

6. **Runpod**
   - API Endpoint on https://api.runpod.ai/v2/k91olo35clkkky/run using a bearer token
   ``` json
   {
   "input": {
         "action": "process_requirements",
         "prompt": "Make me a table for flying",
         "history":[]
      }
   }
   ```

   OR:
   ``` json
   {
   "input": {
      "action": "generate_scad",
      "requirements_json": {
         "constraints": {
         "environment": "outdoor",
         "size": "medium"
         },
         "custom_description": "The custom description for the drone chassis will be generated based on the provided model type.",
         "mobility_type": "flying",
         "model_type": "drone",
         "primary_function": "fast flying drone",
         "standard_components": [
         {
            "constraints": "rotational speed, diameter",
            "name": "Propellers",
            "search_term": "drone propellers"
         },
         {
            "constraints": "power, voltage, size",
            "name": "Motors",
            "search_term": "drone motors"
         },
         {
            "constraints": "capacity, voltage, weight",
            "name": "Battery",
            "search_term": "drone battery"
         },
         {
            "constraints": "processor speed, memory",
            "name": "Flight Controller",
            "search_term": "drone flight controller"
         },
         {
            "constraints": "accuracy, power consumption",
            "name": "GPS Module",
            "search_term": "drone gps module"
         },
         {
            "constraints": "current rating, voltage range",
            "name": "ESC (Electronic Speed Controller)",
            "search_term": "drone esc"
         }
         ]
      }
   }
   }
   ```
      - Returns a response:
      ```
      {
         "id": "74f215e6-835c-4cb2-9311-f33bb377fca9-e2",
         "status": "IN_QUEUE"
      }
      ```
   - API Status Query: https://api.runpod.ai/v2/k91olo35clkkky/status/74f215e6-835c-4cb2-9311-f33bb377fca9-e2
      - Returns previous response if no output
      - Returns output:
      ``` json
         {
            "delayTime": 48730,
            "id": "0477ef6a-7152-4941-86df-836b84951a0e-e2",
            "status": "IN_PROGRESS",
            "workerId": "asfj8lrdwv5ij0"
         }
      ```
      ``` json
      {
         "delayTime": 48730,
         "executionTime": 125620,
         "id": "0477ef6a-7152-4941-86df-836b84951a0e-e2",
         "output": {
            "history": [
               {
                  "content": "Make me a table for flying",
                  "role": "user"
               },
               {
                  "content": "[{\"input\": \"Make me a table for flying\\\"\\n The task is to generate a JSON that matches the required schema for a mechanical assembly project.\\n\\n Steps:\\n 1. The user says \\\"Make me a table for flying\\\". This suggests a flying vehicle, so:\\n    - model_type: likely \\\"drone\\\" (since it's flying and a table might be a structure for a drone, but note: the user says \\\"table\", \"drone\": \"rimary_function:\", \"flying\\\", we choose \\\"drone\": 2.0, \"primary_function\": \"We need a description of the purpose. The user says\", \"flying\": \"It might be a request for a drone that has a table, and the table is for flying (meaning the table is part of the drone). So it's a drone, and drones are often used outdoors.\\n\\n Final decision for constraints:\\n    size:\", \"Make me a table for flying\": \"which might be a request for a drone that has a table (for carrying items). \\n    So the primary function could be:\", \"structure.\": \"ut note: the user might have meant\", \"table\": \"constraints:\", \"payloads.\": \"tandard_components: [\\n    {\", \"standard_components\": \"We need a list of standard parts. For a flying drone with a table, we might have:\\n    - Frame: typically made of carbon fiber or aluminum.\\n    - Motors: for propulsion.\\n    - Propellers: for lift.\\n    - Battery: for power.\\n    - Controller: for flight control.\\n    - Table structure: which might be a flat surface (like a platform) that can be attached to the drone.\\n\\n    However, note the user says\", \"Frame\": \"search_term:\", \"fiber\": \"constraints:\", \"strong\": \"Motors:\", \"Motors\": \"search_term:\", \"size\": \"The user didn't specify, but a typical drone for carrying a table might be medium (since it's not a small toy drone and not a large cargo drone). So\", \"Propellers\": \"search_term:\", \"propeller\": \"constraints:\", \"10x4.5\": \"Battery:\", \"Battery\": \"search_term:\", \"5000mAh\": \"constraints:\", \"7.4V\": \"Table Structure:\", \"Table\": \"search_term:\", \"30cm\": \"ut note: the user might not have specified the exact components, so we are to provide standard ones for a flying drone.\\n\\n 4. custom_description: Detailed description of the frame/chassis. Since it's a flying table, we describe a drone frame that has a table attached. \\n    Example:\", \"access.\": \".\", \"mobility_type\": \"flying\", \"constraints\": \"lightweight, strong\", \"medium\": \"nvironment:\", \"environment\": \"The user didn't specify, but flying drones can be indoor or outdoor. Since it's a table for carrying items, it might be for outdoor use (like delivery). However, the user said\", \"outdoor\": \"ut note: the user might have intended a small drone? However, a table (30cm x 30cm) would require a medium drone.\\n\\n Now, let's build the JSON:\\n\\n model_type:\", \"explanations.\": \"et's set:\\n    size:\", \"flying\\\" in the context of a table is unusual. \\n\\n Considering the ambiguity, we'll choose \\\"outdoor\": \"s the default for a drone that carries a table (like for delivery).\\n\\n But note: the user might have meant a drone that has a table (like a drone with a table for carrying items) and that is typically used outdoors.\\n\\n Alternatively, we can set environment to\", \"name\": \"Drone Frame\", \"search_term\": \"drone frame carbon fiber\"}, {\"name\": \"Payload Table\", \"search_term\": \"drone payload table\", \"constraints\": \"flat, 30cm x 30cm\"}, {\"size\": \"medium\", \"environment\": \"outdoor\"}]",
                  "role": "assistant"
               }
            ],
            "requirements": [
               {
                  "10x4.5": "Battery:",
                  "30cm": "ut note: the user might not have specified the exact components, so we are to provide standard ones for a flying drone.\n\n 4. custom_description: Detailed description of the frame/chassis. Since it's a flying table, we describe a drone frame that has a table attached. \n    Example:",
                  "5000mAh": "constraints:",
                  "7.4V": "Table Structure:",
                  "Battery": "search_term:",
                  "Frame": "search_term:",
                  "Make me a table for flying": "which might be a request for a drone that has a table (for carrying items). \n    So the primary function could be:",
                  "Motors": "search_term:",
                  "Propellers": "search_term:",
                  "Table": "search_term:",
                  "access.": ".",
                  "constraints": "lightweight, strong",
                  "drone": "rimary_function:",
                  "environment": "The user didn't specify, but flying drones can be indoor or outdoor. Since it's a table for carrying items, it might be for outdoor use (like delivery). However, the user said",
                  "explanations.": "et's set:\n    size:",
                  "fiber": "constraints:",
                  "flying": "It might be a request for a drone that has a table, and the table is for flying (meaning the table is part of the drone). So it's a drone, and drones are often used outdoors.\n\n Final decision for constraints:\n    size:",
                  "flying\" in the context of a table is unusual. \n\n Considering the ambiguity, we'll choose \"outdoor": "s the default for a drone that carries a table (like for delivery).\n\n But note: the user might have meant a drone that has a table (like a drone with a table for carrying items) and that is typically used outdoors.\n\n Alternatively, we can set environment to",
                  "flying\", we choose \"drone": 2,
                  "input": "Make me a table for flying\"\n The task is to generate a JSON that matches the required schema for a mechanical assembly project.\n\n Steps:\n 1. The user says \"Make me a table for flying\". This suggests a flying vehicle, so:\n    - model_type: likely \"drone\" (since it's flying and a table might be a structure for a drone, but note: the user says \"table",
                  "medium": "nvironment:",
                  "mobility_type": "flying",
                  "name": "Drone Frame",
                  "outdoor": "ut note: the user might have intended a small drone? However, a table (30cm x 30cm) would require a medium drone.\n\n Now, let's build the JSON:\n\n model_type:",
                  "payloads.": "tandard_components: [\n    {",
                  "primary_function": "We need a description of the purpose. The user says",
                  "propeller": "constraints:",
                  "search_term": "drone frame carbon fiber",
                  "size": "The user didn't specify, but a typical drone for carrying a table might be medium (since it's not a small toy drone and not a large cargo drone). So",
                  "standard_components": "We need a list of standard parts. For a flying drone with a table, we might have:\n    - Frame: typically made of carbon fiber or aluminum.\n    - Motors: for propulsion.\n    - Propellers: for lift.\n    - Battery: for power.\n    - Controller: for flight control.\n    - Table structure: which might be a flat surface (like a platform) that can be attached to the drone.\n\n    However, note the user says",
                  "strong": "Motors:",
                  "structure.": "ut note: the user might have meant",
                  "table": "constraints:"
               },
               {
                  "constraints": "flat, 30cm x 30cm",
                  "name": "Payload Table",
                  "search_term": "drone payload table"
               },
               {
                  "environment": "outdoor",
                  "size": "medium"
               }
            ],
            "status": "success"
         },
         "status": "COMPLETED",
         "workerId": "asfj8lrdwv5ij0"
      } ```

## Deliverables
- Working FastAPI service with API routes.
- Database models + migrations.
- Docs and operational setup.
- Starter tests and CI-ready structure.

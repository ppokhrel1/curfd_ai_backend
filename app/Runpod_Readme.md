# curfdai_ml API

This service is a RunPod Serverless handler. The HTTP endpoint is provided by RunPod for your deployed worker. All API calls send a JSON body with an `input` object that includes an `action` field.

## API Paths (RunPod Serverless)

- `POST /run` (async job submission)
- `POST /runsync` (synchronous request)

## Base Request Shape

```json
{
  "input": {
    "action": "...",
    "...": "..."
  }
}
```

## Actions

### 1) process_requirements
Refines a user's prompt into a structured requirements JSON and returns updated chat history.

**Request payload**

```json
{
  "input": {
    "action": "process_requirements",
    "prompt": "Design a small quadcopter frame",
    "history": [
      {"role": "user", "content": "I need a lightweight drone"},
      {"role": "assistant", "content": "{\"model_type\":\"drone\",...}"}
    ]
  }
}
```

**Response body (success)**

```json
{
  "status": "success",
  "requirements": {
    "model_type": "drone",
    "primary_function": "A small quadcopter frame for hobby use",
    "description_natural_language": "A compact central body with four arms equally spaced...",
    "standard_components": [
      {"name": "Flight controller", "search_term": "stack 20x20 mm"}
    ],
    "custom_description": "Use 3 mm thick arms, 160 mm motor-to-motor..."
  },
  "history": [
    {"role": "user", "content": "Design a small quadcopter frame"},
    {"role": "assistant", "content": "{\"model_type\":\"drone\",...}"}
  ]
}
```

**Notes**
- `history` is optional. If omitted, it defaults to an empty list.
- `requirements` follows this schema:
  - `model_type`: `drone | robot | car | custom`
  - `primary_function`: string
  - `description_natural_language`: string
  - `standard_components`: array of `{ name, search_term }`
  - `custom_description`: string

---

### 2) generate_scad
Generates OpenSCAD output and uploads a zipped package to B2.

**Request payload**

```json
{
  "input": {
    "action": "generate_scad",
    "requirements_json": {
      "model_type": "drone",
      "primary_function": "A small quadcopter frame for hobby use",
      "description_natural_language": "A compact central body with four arms equally spaced...",
      "standard_components": [
        {"name": "Flight controller", "search_term": "stack 20x20 mm"}
      ],
      "custom_description": "Use 3 mm thick arms, 160 mm motor-to-motor..."
    }
  }
}
```

**Response body (success)**

```json
{
  "status": "success",
  "data": {
    "file_id": "b2_file_id",
    "filename": "assembly.zip",
    "download_url": "https://..."
  }
}
```

**Response body (error)**

```json
{
  "error": "No requirements_json provided"
}
```

**Live run (2026-02-03)**

**Request payload**

```json
{
  "input": {
    "action": "generate_scad",
    "requirements_json": {
      "model_type": "drone",
      "primary_function": "A small quadcopter frame for hobby use",
      "description_natural_language": "A compact central body with four arms equally spaced.",
      "standard_components": [
        {"name": "Flight controller", "search_term": "stack 20x20 mm"}
      ],
      "custom_description": "Use 3 mm thick arms, 160 mm motor-to-motor."
    }
  }
}
```

**Response body (success)**

```json
{
  "delayTime": 104,
  "executionTime": 34246,
  "id": "50f014ef-ed19-4d2e-839b-2469d74d9c8a-e1",
  "output": {
    "data": {
      "download_url": "https://f005.backblazeb2.com/file/nooriat-models/user_outputs/drone_810f790e.zip?Authorization=3_20260203022910_d5068cb85ccf28f9703a7704_45c5a48bf090a9fdbae3ced6481c202a71ab009d_005_20260204022910_0031_dnld",
      "file_id": "4_z423ae7cad396f7f496bc0f17_f103acab438ad3773_d20260203_m022910_c005_v0501039_t0048_u01770085750642",
      "filename": "user_outputs/drone_810f790e.zip"
    },
    "status": "success"
  },
  "status": "COMPLETED",
  "workerId": "74haeo0ymyio5c"
}
```

---

### 3) health
Returns a basic health status and GPU availability.

**Request payload**

```json
{
  "input": {
    "action": "health"
  }
}
```

**Response body**

```json
{
  "status": "healthy",
  "gpu": true,
  "service_initialized": true
}
```

## Error Format
If an unexpected exception occurs, the handler returns:

```json
{
  "error": "<error message>"
}
```
